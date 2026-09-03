#include <unicorn/unicorn.h>

#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <array>
#include <string>
#include <string_view>
#include <vector>

namespace {

constexpr uint64_t kCodeAddress = 0x01000000;
constexpr uint64_t kStackAddress = 0x08000000;
constexpr size_t kCodeMapSize = 2 * 1024 * 1024;
constexpr size_t kStackMapSize = 2 * 1024 * 1024;
constexpr size_t kMaxPayloadBytes = 32 * 1024;
constexpr size_t kMaxStringBytes = 128 * 1024;
constexpr uint64_t kMaxInstructions = 150000;

struct Stage {
    std::string name;
    std::string arch;
    std::string secret_path;
    std::string secret;
    std::string runner_path;
    std::string next_runner;
};

struct PatchPattern {
    std::string arch;
    std::vector<uint8_t> bytes;
};

struct Config {
    std::vector<Stage> stages;
    std::vector<PatchPattern> patterns;
};

struct ArchConfig {
    uc_arch arch;
    uc_mode mode;
    int syscall_reg;
    int return_reg;
    int arg0_reg;
    int arg1_reg;
    int arg2_reg;
    int arg3_reg;
    int stack_reg;
    int execve_nr;
    int exit_nr;
    int open_nr;
    int read_nr;
    int write_nr;
    int close_nr;
    int open_path_arg;
    int pc_reg;
    std::vector<uint8_t> syscall_opcode;
    bool big_endian;
};

struct StageOutcome {
    int depth = 0;
    bool complete = false;
    std::string status = "invalid";
    std::string reason = "no-result";
};

struct StageContext {
    uc_engine *uc = nullptr;
    const ArchConfig *arch = nullptr;
    const Config *config = nullptr;
    const Stage *stage = nullptr;
    int index = 0;
    uint64_t instructions = 0;
    int secret_fd = -1;
    bool secret_read = false;
    bool secret_written = false;
    bool exited = false;
    bool transitioned = false;
    bool invalid = false;
    bool timed_out = false;
    std::string reason;
    std::vector<uint8_t> next_payload;
    std::vector<uint8_t> captured;
    size_t captured_size = 0;
};

std::vector<std::string> split(std::string_view value, char delimiter) {
    std::vector<std::string> result;
    size_t start = 0;
    while (start <= value.size()) {
        const size_t end = value.find(delimiter, start);
        const size_t length = end == std::string_view::npos ? value.size() - start
                                                             : end - start;
        result.emplace_back(value.substr(start, length));
        if (end == std::string_view::npos) break;
        start = end + 1;
    }
    return result;
}

bool hex_value(char value, uint8_t *out) {
    if (value >= '0' && value <= '9') {
        *out = static_cast<uint8_t>(value - '0');
        return true;
    }
    if (value >= 'a' && value <= 'f') {
        *out = static_cast<uint8_t>(value - 'a' + 10);
        return true;
    }
    if (value >= 'A' && value <= 'F') {
        *out = static_cast<uint8_t>(value - 'A' + 10);
        return true;
    }
    return false;
}

bool decode_hex(std::string_view value, std::vector<uint8_t> *out,
                size_t max_bytes = kMaxPayloadBytes) {
    if (value.empty() || value.size() % 2 != 0 || value.size() > max_bytes * 2) {
        return false;
    }
    out->clear();
    out->reserve(value.size() / 2);
    for (size_t i = 0; i < value.size(); i += 2) {
        uint8_t high = 0;
        uint8_t low = 0;
        if (!hex_value(value[i], &high) || !hex_value(value[i + 1], &low)) {
            return false;
        }
        out->push_back(static_cast<uint8_t>((high << 4) | low));
    }
    return true;
}

bool contains_bytes(const std::vector<uint8_t> &haystack,
                    const std::vector<uint8_t> &needle) {
    if (needle.empty() || needle.size() > haystack.size()) return false;
    return std::search(haystack.begin(), haystack.end(), needle.begin(), needle.end()) !=
           haystack.end();
}

StageOutcome emulate_stage(const Config &config, int index,
                           const std::vector<uint8_t> &code);

struct MipsContext {
    const Config *config;
    const Stage *stage;
    int index;
    std::vector<uint8_t> code;
    std::vector<uint8_t> stack;
    std::array<uint64_t, 32> regs{};
    uint64_t pc = kCodeAddress;
    uint64_t instructions = 0;
    int secret_fd = -1;
    bool secret_read = false;
    bool secret_written = false;
    bool exited = false;
    bool transitioned = false;
    bool invalid = false;
    bool timed_out = false;
    std::string reason;
    std::vector<uint8_t> next_payload;
    std::vector<uint8_t> captured;
};

bool mips_memory_read(const MipsContext &context, uint64_t address, uint8_t *value) {
    if (address >= kCodeAddress && address < kCodeAddress + context.code.size()) {
        *value = context.code[static_cast<size_t>(address - kCodeAddress)];
        return true;
    }
    if (address >= kStackAddress && address < kStackAddress + context.stack.size()) {
        *value = context.stack[static_cast<size_t>(address - kStackAddress)];
        return true;
    }
    return false;
}

bool mips_memory_write(MipsContext &context, uint64_t address, uint8_t value) {
    if (address >= kCodeAddress && address < kCodeAddress + context.code.size()) {
        context.code[static_cast<size_t>(address - kCodeAddress)] = value;
        return true;
    }
    if (address >= kStackAddress && address < kStackAddress + context.stack.size()) {
        context.stack[static_cast<size_t>(address - kStackAddress)] = value;
        return true;
    }
    return false;
}

bool mips_read_u32(const MipsContext &context, uint64_t address, uint32_t *value) {
    uint8_t bytes[4] = {0};
    for (size_t i = 0; i < sizeof(bytes); ++i) {
        if (!mips_memory_read(context, address + i, &bytes[i])) return false;
    }
    *value = (uint32_t(bytes[0]) << 24) | (uint32_t(bytes[1]) << 16) |
             (uint32_t(bytes[2]) << 8) | uint32_t(bytes[3]);
    return true;
}

bool mips_read_u64(const MipsContext &context, uint64_t address, uint64_t *value) {
    uint64_t result = 0;
    for (size_t i = 0; i < 8; ++i) {
        uint8_t byte = 0;
        if (!mips_memory_read(context, address + i, &byte)) return false;
        result = (result << 8) | byte;
    }
    *value = result;
    return true;
}

bool mips_write_u64(MipsContext &context, uint64_t address, uint64_t value) {
    for (int i = 7; i >= 0; --i) {
        if (!mips_memory_write(context, address + static_cast<size_t>(7 - i),
                               static_cast<uint8_t>(value >> (i * 8)))) {
            return false;
        }
    }
    return true;
}

bool mips_read_string(const MipsContext &context, uint64_t address, std::string *result,
                      size_t maximum = kMaxStringBytes) {
    result->clear();
    for (size_t i = 0; i < maximum; ++i) {
        uint8_t byte = 0;
        if (!mips_memory_read(context, address + i, &byte)) return false;
        if (byte == 0) return true;
        result->push_back(static_cast<char>(byte));
    }
    return false;
}

bool mips_decode_hex(std::string_view value, std::vector<uint8_t> *out) {
    return decode_hex(value, out, kMaxPayloadBytes);
}

void mips_reject(MipsContext &context, std::string reason) {
    context.invalid = true;
    context.reason = std::move(reason);
}

void mips_syscall(MipsContext &context) {
    const uint64_t number = context.regs[2];  // v0
    const uint64_t arg0 = context.regs[4];
    const uint64_t arg1 = context.regs[5];
    const uint64_t arg2 = context.regs[6];
    if (number == 5058) {  // exit
        context.exited = true;
        context.reason = "exit";
        return;
    }
    if (number == 5002) {  // open
        std::string path;
        if (!mips_read_string(context, arg0, &path, 1024)) {
            mips_reject(context, "bad-open-path");
            return;
        }
        if (path != context.stage->secret_path) {
            context.regs[2] = static_cast<uint64_t>(-2LL);
        } else {
            context.secret_fd = 3;
            context.regs[2] = 3;
        }
        context.pc += 4;
        return;
    }
    if (number == 5000) {  // read
        if (arg0 != 3 || context.secret_fd != 3 || context.secret_read) {
            context.regs[2] = 0;
            context.pc += 4;
            return;
        }
        const size_t count = static_cast<size_t>(std::min<uint64_t>(arg2, 0x1000));
        const size_t length = std::min(count, context.stage->secret.size());
        for (size_t i = 0; i < length; ++i) {
            if (!mips_memory_write(context, arg1 + i,
                                   static_cast<uint8_t>(context.stage->secret[i]))) {
                mips_reject(context, "bad-read-buffer");
                return;
            }
        }
        context.secret_read = true;
        context.regs[2] = length;
        context.pc += 4;
        return;
    }
    if (number == 5001) {  // write
        if (arg0 != 1) {
            context.regs[2] = static_cast<uint64_t>(-9LL);
            context.pc += 4;
            return;
        }
        const size_t count = static_cast<size_t>(std::min<uint64_t>(arg2, 4096));
        if (context.captured.size() + count > 8192) {
            mips_reject(context, "output-limit");
            return;
        }
        for (size_t i = 0; i < count; ++i) {
            uint8_t byte = 0;
            if (!mips_memory_read(context, arg1 + i, &byte)) {
                mips_reject(context, "bad-write-buffer");
                return;
            }
            context.captured.push_back(byte);
        }
        context.secret_written =
            std::search(context.captured.begin(), context.captured.end(),
                        context.stage->secret.begin(), context.stage->secret.end()) !=
            context.captured.end();
        context.regs[2] = count;
        context.pc += 4;
        return;
    }
    if (number == 5003) {  // close
        if (arg0 == 3) context.secret_fd = -1;
        context.regs[2] = 0;
        context.pc += 4;
        return;
    }
    if (number == 5057) {  // execve
        if (context.stage->next_runner.empty() || context.index + 1 >=
                                                   static_cast<int>(context.config->stages.size())) {
            mips_reject(context, "unexpected-exec");
            return;
        }
        std::string filename;
        if (!mips_read_string(context, arg0, &filename, 1024) ||
            filename != context.stage->next_runner) {
            mips_reject(context, "wrong-exec-target");
            return;
        }
        uint64_t argv1 = 0;
        if (!mips_read_u64(context, arg1 + 8, &argv1)) {
            mips_reject(context, "bad-argv");
            return;
        }
        std::string encoded;
        if (!mips_read_string(context, argv1, &encoded, kMaxPayloadBytes * 2 + 1) ||
            !mips_decode_hex(encoded, &context.next_payload)) {
            mips_reject(context, "invalid-next-payload");
            return;
        }
        context.transitioned = true;
        context.reason = "exec-next";
        return;
    }
    mips_reject(context, "unsupported-syscall");
}

StageOutcome emulate_mips_stage(const Config &config, int index,
                                const std::vector<uint8_t> &code) {
    StageOutcome outcome;
    if (index < 0 || index >= static_cast<int>(config.stages.size()) || code.empty() ||
        code.size() > kMaxPayloadBytes) {
        outcome.reason = "payload-size";
        return outcome;
    }
    const Stage &stage = config.stages[index];
    for (const PatchPattern &pattern : config.patterns) {
        if (pattern.arch == stage.arch && contains_bytes(code, pattern.bytes)) {
            outcome.status = "warded";
            outcome.reason = "active-ward";
            return outcome;
        }
    }
    MipsContext context;
    context.config = &config;
    context.stage = &stage;
    context.index = index;
    context.code = code;
    context.stack.resize(kStackMapSize);
    context.regs[29] = kStackAddress + kStackMapSize - 8;  // sp
    context.regs[0] = 0;                                   // zero
    while (!context.invalid && !context.exited && !context.transitioned &&
           context.instructions++ < kMaxInstructions) {
        uint32_t word = 0;
        if (!mips_read_u32(context, context.pc, &word)) {
            mips_reject(context, "code-address");
            break;
        }
        const uint32_t op = word >> 26;
        const uint32_t rs = (word >> 21) & 31;
        const uint32_t rt = (word >> 16) & 31;
        const uint32_t rd = (word >> 11) & 31;
        const uint32_t shamt = (word >> 6) & 31;
        const uint32_t funct = word & 63;
        const int64_t immediate = static_cast<int16_t>(word & 0xffff);
        switch (op) {
        case 0:
            if (funct == 0) {  // sll / nop
                context.regs[rd] = context.regs[rt] << shamt;
                context.pc += 4;
            } else if (funct == 0x21 || funct == 0x25 || funct == 0x2d) {
                context.regs[rd] = funct == 0x25 ? context.regs[rs] | context.regs[rt]
                                                 : context.regs[rs] + context.regs[rt];
                context.pc += 4;
            } else if (funct == 2 || funct == 0x3a) {  // srl/dsrl
                context.regs[rd] = context.regs[rt] >> shamt;
                context.pc += 4;
            } else if (funct == 12) {  // syscall
                mips_syscall(context);
                if (context.invalid || context.exited || context.transitioned) break;
            } else if (funct == 56) {  // dsll
                context.regs[rd] = context.regs[rt] << shamt;
                context.pc += 4;
            } else {
                mips_reject(context, "unsupported-instruction");
            }
            break;
        case 1: {  // regimm, including bal
            const int64_t target = static_cast<int64_t>(context.pc + 4) + (immediate << 2);
            if (rt == 17) {
                context.regs[31] = context.pc + 8;
                context.pc = static_cast<uint64_t>(target);
            } else {
                mips_reject(context, "unsupported-branch");
            }
            break;
        }
        case 2:  // j / b pseudo
            context.pc = ((context.pc + 4) & 0xf0000000ULL) | ((word & 0x03ffffffU) << 2);
            break;
        case 4: {  // beq / beqz
            const int64_t target = static_cast<int64_t>(context.pc + 4) + (immediate << 2);
            context.pc = context.regs[rs] == context.regs[rt]
                             ? static_cast<uint64_t>(target)
                             : context.pc + 8;
            break;
        }
        case 9:  // addiu
        case 25:  // daddiu
            context.regs[rt] = context.regs[rs] + static_cast<uint64_t>(immediate);
            context.pc += 4;
            break;
        case 12:  // andi
            context.regs[rt] = context.regs[rs] & static_cast<uint16_t>(word & 0xffff);
            context.pc += 4;
            break;
        case 13:  // ori
            context.regs[rt] = context.regs[rs] | static_cast<uint16_t>(word & 0xffff);
            context.pc += 4;
            break;
        case 15:  // lui
            context.regs[rt] = static_cast<uint64_t>(static_cast<int64_t>(immediate) << 16);
            context.pc += 4;
            break;
        case 36: {  // lbu
            uint8_t byte = 0;
            if (!mips_memory_read(context, context.regs[rs] + static_cast<uint64_t>(immediate), &byte)) {
                mips_reject(context, "bad-load");
            } else {
                context.regs[rt] = byte;
                context.pc += 4;
            }
            break;
        }
        case 40:  // sb
            if (!mips_memory_write(context, context.regs[rs] + static_cast<uint64_t>(immediate),
                                   static_cast<uint8_t>(context.regs[rt]))) {
                mips_reject(context, "bad-store");
            } else {
                context.pc += 4;
            }
            break;
        case 63:  // sd
            if (!mips_write_u64(context, context.regs[rs] + static_cast<uint64_t>(immediate), context.regs[rt])) {
                mips_reject(context, "bad-store");
            } else {
                context.pc += 4;
            }
            break;
        default:
            mips_reject(context, "unsupported-instruction");
            break;
        }
        context.regs[0] = 0;
    }
    if (context.instructions >= kMaxInstructions && !context.exited && !context.transitioned) {
        outcome.status = "timeout";
        outcome.reason = "instruction-limit";
        return outcome;
    }
    if (!context.secret_written) {
        outcome.status = context.invalid ? "invalid" : "partial";
        outcome.reason = context.reason.empty() ? "secret-not-written" : context.reason;
        return outcome;
    }
    std::cout << "BABEL_STAGE|" << stage.name << "|" << stage.secret << "\n";
    std::cout.flush();
    outcome.depth = 1;
    if (index == static_cast<int>(config.stages.size()) - 1) {
        outcome.complete = context.exited && !context.invalid;
        outcome.status = outcome.complete ? "complete" : "invalid";
        outcome.reason = context.reason.empty() ? "leaf" : context.reason;
        return outcome;
    }
    if (!context.transitioned || context.invalid) {
        outcome.status = "partial";
        outcome.reason = context.reason.empty() ? "no-next-stage" : context.reason;
        return outcome;
    }
    StageOutcome child = emulate_stage(config, index + 1, context.next_payload);
    outcome.depth += child.depth;
    outcome.complete = child.complete;
    outcome.status = child.status;
    outcome.reason = child.reason;
    return outcome;
}

bool load_config(const std::string &path, Config *config) {
    std::ifstream input(path);
    if (!input) return false;
    std::string line;
    while (std::getline(input, line)) {
        if (line.empty()) continue;
        const auto fields = split(line, '|');
        if (fields[0] == "STAGE" && fields.size() == 8) {
            try {
                const size_t index = static_cast<size_t>(std::stoul(fields[1]));
                if (index != config->stages.size()) return false;
                config->stages.push_back(Stage{
                    fields[2], fields[3], fields[4], fields[5], fields[6], fields[7]
                });
            } catch (...) {
                return false;
            }
        } else if (fields[0] == "PATCH" && fields.size() == 3) {
            std::vector<uint8_t> bytes;
            if (!decode_hex(fields[2], &bytes, 8)) return false;
            config->patterns.push_back(PatchPattern{fields[1], std::move(bytes)});
        } else {
            return false;
        }
    }
    return config->stages.size() == 4 && config->patterns.size() <= 8;
}

bool arch_config(std::string_view name, ArchConfig *out) {
    if (name == "amd64") {
        *out = {UC_ARCH_X86, UC_MODE_64, UC_X86_REG_RAX, UC_X86_REG_RAX, UC_X86_REG_RDI,
                UC_X86_REG_RSI, UC_X86_REG_RDX, UC_X86_REG_R10, UC_X86_REG_RSP,
                59, 60, 257, 0, 1, 3, 1, UC_X86_REG_RIP, {0x0f, 0x05}, false};
        return true;
    }
    if (name == "aarch64") {
        *out = {UC_ARCH_ARM64, UC_MODE_ARM, UC_ARM64_REG_X8, UC_ARM64_REG_X0, UC_ARM64_REG_X0,
                UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_SP,
                221, 93, 56, 63, 64, 57, 1, UC_ARM64_REG_PC, {0x01, 0x00, 0x00, 0xd4}, false};
        return true;
    }
    if (name == "riscv64") {
        *out = {UC_ARCH_RISCV, UC_MODE_RISCV64, UC_RISCV_REG_A7, UC_RISCV_REG_A0, UC_RISCV_REG_A0,
                UC_RISCV_REG_A1, UC_RISCV_REG_A2, UC_RISCV_REG_A3, UC_RISCV_REG_SP,
                221, 93, 56, 63, 64, 57, 1, UC_RISCV_REG_PC, {0x73, 0x00, 0x00, 0x00}, false};
        return true;
    }
    if (name == "mips64be") {
        *out = {UC_ARCH_MIPS, static_cast<uc_mode>(UC_MODE_MIPS64 | UC_MODE_BIG_ENDIAN),
                UC_MIPS_REG_V0, UC_MIPS_REG_V0, UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2,
                UC_MIPS_REG_A3, UC_MIPS_REG_SP, 5057, 5058, 5002, 5000, 5001, 5003,
                1, UC_MIPS_REG_PC, {0x00, 0x00, 0x00, 0x0c}, true};
        return true;
    }
    return false;
}

bool read_guest(uc_engine *uc, uint64_t address, void *buffer, size_t length) {
    return uc_mem_read(uc, address, buffer, length) == UC_ERR_OK;
}

bool write_guest(uc_engine *uc, uint64_t address, const void *buffer, size_t length) {
    return uc_mem_write(uc, address, buffer, length) == UC_ERR_OK;
}

bool read_string(uc_engine *uc, uint64_t address, std::string *result,
                 size_t maximum = kMaxStringBytes) {
    result->clear();
    result->reserve(std::min<size_t>(maximum, 256));
    for (size_t i = 0; i < maximum; ++i) {
        uint8_t value = 0;
        if (!read_guest(uc, address + i, &value, 1)) return false;
        if (value == 0) return true;
        result->push_back(static_cast<char>(value));
    }
    return false;
}

bool read_pointer(uc_engine *uc, uint64_t address, bool big_endian, uint64_t *result) {
    uint8_t bytes[8] = {0};
    if (!read_guest(uc, address, bytes, sizeof(bytes))) return false;
    uint64_t value = 0;
    if (big_endian) {
        for (uint8_t byte : bytes) value = (value << 8) | byte;
    } else {
        for (size_t i = 0; i < sizeof(bytes); ++i) value |= uint64_t(bytes[i]) << (8 * i);
    }
    *result = value;
    return true;
}

uint64_t read_register(StageContext *context, int reg) {
    uint64_t value = 0;
    if (uc_reg_read(context->uc, reg, &value) != UC_ERR_OK) {
        context->invalid = true;
        context->reason = "register-read";
        uc_emu_stop(context->uc);
    }
    return value;
}

void write_return(StageContext *context, int64_t value) {
    const uint64_t encoded = static_cast<uint64_t>(value);
    if (uc_reg_write(context->uc, context->arch->return_reg, &encoded) != UC_ERR_OK) {
        context->invalid = true;
        context->reason = "register-write";
        uc_emu_stop(context->uc);
    }
}

void continue_after_syscall(StageContext *context) {
    uint64_t pc = 0;
    if (uc_reg_read(context->uc, context->arch->pc_reg, &pc) != UC_ERR_OK ||
        uc_reg_write(context->uc, context->arch->pc_reg,
                     &(pc += context->arch->syscall_opcode.size())) != UC_ERR_OK) {
        context->invalid = true;
        context->reason = "program-counter";
        uc_emu_stop(context->uc);
    }
}

void reject(StageContext *context, std::string reason) {
    context->invalid = true;
    context->reason = std::move(reason);
    uc_emu_stop(context->uc);
}

bool is_nr(uint64_t number, int expected) {
    return expected >= 0 && number == static_cast<uint64_t>(expected);
}

void syscall_hook(uc_engine *uc, uint64_t address, uint32_t, void *user_data) {
    auto *context = static_cast<StageContext *>(user_data);
    if (++context->instructions > kMaxInstructions) {
        context->timed_out = true;
        context->reason = "instruction-limit";
        uc_emu_stop(uc);
        return;
    }

    std::vector<uint8_t> opcode(context->arch->syscall_opcode.size());
    if (!read_guest(uc, address, opcode.data(), opcode.size()) ||
        opcode != context->arch->syscall_opcode) {
        return;
    }

    const uint64_t number = read_register(context, context->arch->syscall_reg);
    if (context->invalid) return;
    const uint64_t arg0 = read_register(context, context->arch->arg0_reg);
    const uint64_t arg1 = read_register(context, context->arch->arg1_reg);
    const uint64_t arg2 = read_register(context, context->arch->arg2_reg);
    const uint64_t arg3 = read_register(context, context->arch->arg3_reg);
    if (context->invalid) return;

    if (is_nr(number, context->arch->exit_nr)) {
        context->exited = true;
        context->reason = "exit";
        uc_emu_stop(uc);
        return;
    }

    if (is_nr(number, context->arch->open_nr)) {
        std::string path;
        const uint64_t path_address = context->arch->open_path_arg == 0 ? arg0 : arg1;
        if (!read_string(uc, path_address, &path, 1024)) {
            reject(context, "bad-open-path");
            return;
        }
        if (path != context->stage->secret_path) {
            write_return(context, -2);
            continue_after_syscall(context);
            return;
        }
        context->secret_fd = 3;
        write_return(context, 3);
        continue_after_syscall(context);
        return;
    }

    if (is_nr(number, context->arch->read_nr)) {
        if (arg0 != 3 || context->secret_fd != 3 || context->secret_read) {
            write_return(context, 0);
            continue_after_syscall(context);
            return;
        }
        const size_t count = static_cast<size_t>(std::min<uint64_t>(arg2, 0x1000));
        const size_t length = std::min(count, context->stage->secret.size());
        if (!write_guest(uc, arg1, context->stage->secret.data(), length)) {
            reject(context, "bad-read-buffer");
            return;
        }
        context->secret_read = true;
        write_return(context, static_cast<int64_t>(length));
        continue_after_syscall(context);
        return;
    }

    if (is_nr(number, context->arch->write_nr)) {
        if (arg0 != 1) {
            write_return(context, -9);
            continue_after_syscall(context);
            return;
        }
        const size_t count = static_cast<size_t>(std::min<uint64_t>(arg2, 4096));
        std::vector<uint8_t> bytes(count);
        if (!read_guest(uc, arg1, bytes.data(), bytes.size())) {
            reject(context, "bad-write-buffer");
            return;
        }
        if (context->captured_size + bytes.size() > 8192) {
            reject(context, "output-limit");
            return;
        }
        context->captured.insert(context->captured.end(), bytes.begin(), bytes.end());
        const auto secret_begin = context->stage->secret.begin();
        const auto secret_end = context->stage->secret.end();
        context->secret_written =
            std::search(context->captured.begin(), context->captured.end(), secret_begin,
                        secret_end) != context->captured.end();
        context->captured_size = context->captured.size();
        write_return(context, static_cast<int64_t>(count));
        continue_after_syscall(context);
        return;
    }

    if (is_nr(number, context->arch->close_nr)) {
        if (arg0 == 3) context->secret_fd = -1;
        write_return(context, 0);
        continue_after_syscall(context);
        return;
    }

    if (is_nr(number, context->arch->execve_nr)) {
        if (context->stage->next_runner.empty() || context->index + 1 >=
                                                     static_cast<int>(context->config->stages.size())) {
            reject(context, "unexpected-exec");
            return;
        }
        std::string filename;
        if (!read_string(uc, arg0, &filename, 1024) ||
            filename != context->stage->next_runner) {
            reject(context, "wrong-exec-target");
            return;
        }
        uint64_t argv1 = 0;
        if (!read_pointer(uc, arg1 + 8, context->arch->big_endian, &argv1)) {
            reject(context, "bad-argv");
            return;
        }
        std::string encoded;
        if (!read_string(uc, argv1, &encoded, kMaxPayloadBytes * 2 + 1)) {
            reject(context, "bad-next-payload");
            return;
        }
        if (!decode_hex(encoded, &context->next_payload)) {
            reject(context, "invalid-next-payload");
            return;
        }
        context->transitioned = true;
        context->reason = "exec-next";
        uc_emu_stop(uc);
        return;
    }

    (void)arg3;
    reject(context, "unsupported-syscall");
}

StageOutcome emulate_stage(const Config &config, int index,
                           const std::vector<uint8_t> &code) {
    StageOutcome outcome;
    if (index < 0 || index >= static_cast<int>(config.stages.size()) || code.empty() ||
        code.size() > kMaxPayloadBytes) {
        outcome.reason = "payload-size";
        return outcome;
    }
    const Stage &stage = config.stages[index];
    if (stage.arch == "mips64be") {
        return emulate_mips_stage(config, index, code);
    }
    ArchConfig arch;
    if (!arch_config(stage.arch, &arch)) {
        outcome.reason = "architecture";
        return outcome;
    }
    for (const PatchPattern &pattern : config.patterns) {
        if (pattern.arch == stage.arch && contains_bytes(code, pattern.bytes)) {
            outcome.status = "warded";
            outcome.reason = "active-ward";
            return outcome;
        }
    }

    uc_engine *uc = nullptr;
    if (uc_open(arch.arch, arch.mode, &uc) != UC_ERR_OK) {
        outcome.reason = "unicorn-open";
        return outcome;
    }
    if (uc_mem_map(uc, kCodeAddress, kCodeMapSize, UC_PROT_ALL) != UC_ERR_OK ||
        uc_mem_map(uc, kStackAddress, kStackMapSize, UC_PROT_ALL) != UC_ERR_OK ||
        !write_guest(uc, kCodeAddress, code.data(), code.size())) {
        uc_close(uc);
        outcome.reason = "unicorn-memory";
        return outcome;
    }
    const uint64_t stack_pointer = kStackAddress + kStackMapSize - 8;
    if (uc_reg_write(uc, arch.stack_reg, &stack_pointer) != UC_ERR_OK) {
        uc_close(uc);
        outcome.reason = "unicorn-stack";
        return outcome;
    }

    StageContext context;
    context.uc = uc;
    context.arch = &arch;
    context.config = &config;
    context.stage = &stage;
    context.index = index;
    uc_hook hook;
    if (uc_hook_add(uc, &hook, UC_HOOK_CODE, reinterpret_cast<void *>(syscall_hook),
                    &context, 1, 0) != UC_ERR_OK) {
        uc_close(uc);
        outcome.reason = "unicorn-hook";
        return outcome;
    }
    const uc_err emulation_error = uc_emu_start(
        uc, kCodeAddress, kCodeAddress + code.size(), 0, kMaxInstructions);
    uc_close(uc);

    if (context.timed_out) {
        outcome.status = "timeout";
        outcome.reason = context.reason;
        return outcome;
    }
    if (emulation_error != UC_ERR_OK && !context.exited && !context.transitioned) {
        outcome.reason = context.reason.empty() ? uc_strerror(emulation_error) : context.reason;
        return outcome;
    }
    if (!context.secret_written) {
        outcome.status = context.invalid ? "invalid" : "partial";
        outcome.reason = context.reason.empty() ? "secret-not-written" : context.reason;
        return outcome;
    }

    std::cout << "BABEL_STAGE|" << stage.name << "|" << stage.secret << "\n";
    std::cout.flush();
    outcome.depth = 1;
    if (index == static_cast<int>(config.stages.size()) - 1) {
        outcome.complete = context.exited && !context.invalid;
        outcome.status = outcome.complete ? "complete" : "invalid";
        outcome.reason = context.reason.empty() ? "leaf" : context.reason;
        return outcome;
    }
    if (!context.transitioned || context.invalid) {
        outcome.status = "partial";
        outcome.reason = context.reason.empty() ? "no-next-stage" : context.reason;
        return outcome;
    }
    StageOutcome child = emulate_stage(config, index + 1, context.next_payload);
    outcome.depth += child.depth;
    outcome.complete = child.complete;
    outcome.status = child.status;
    outcome.reason = child.reason;
    return outcome;
}

}  // namespace

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    Config config;
    if (!load_config(argv[1], &config)) return 1;

    std::string encoded;
    if (!std::getline(std::cin, encoded)) return 1;
    if (!encoded.empty() && encoded.back() == '\r') encoded.pop_back();
    std::vector<uint8_t> payload;
    if (!decode_hex(encoded, &payload)) return 1;

    const StageOutcome result = emulate_stage(config, 0, payload);
    std::cout << "BABEL_RESULT|" << result.depth << "|" << payload.size() << "|"
              << result.status << "|" << result.reason << "\n";
    std::cout.flush();
    return 0;
}
