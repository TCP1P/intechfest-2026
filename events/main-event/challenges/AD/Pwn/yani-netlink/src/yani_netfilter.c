#include <linux/cred.h>
#include <linux/errno.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/mutex.h>
#include <linux/string.h>
#include <linux/types.h>

#define YANI_MAX_DATA 256
#define YANI_HOOK_WINDOW (5 * HZ)

struct yani_state {
	unsigned char storage[64];
	unsigned long function;
	unsigned long argument;
	unsigned char tail[384];
};

struct yani_read_frame {
	unsigned char buffer[64];
	unsigned long prepare;
	unsigned long commit;
	unsigned long init;
};

static struct yani_state yani_state;
static DEFINE_MUTEX(yani_state_lock);
static atomic64_t yani_last_hook = ATOMIC64_INIT(0);

static const char yani_smoking_message[] =
	"Yani is a catgirl with a seriously bad smoking habit. She smokes so much "
	"that her apartment smells like ash and is littered with cigarette butts—"
	"and plenty of other trash! Every time she tries to quit, she becomes weak "
	"to the cravings and gives in almost instantly. Will she ever get her life "
	"together, or is she doomed to live as a chainsmoking slob forever?";

static int yani_hex_value(char byte)
{
	if (byte >= '0' && byte <= '9')
		return byte - '0';
	if (byte >= 'a' && byte <= 'f')
		return byte - 'a' + 10;
	if (byte >= 'A' && byte <= 'F')
		return byte - 'A' + 10;
	return -1;
}

static int yani_hex_decode(const char *hex, unsigned char *data, size_t length)
{
	size_t index;

	if (strlen(hex) != length * 2)
		return -EINVAL;
	for (index = 0; index < length; ++index) {
		int high = yani_hex_value(hex[index * 2]);
		int low = yani_hex_value(hex[index * 2 + 1]);

		if (high < 0 || low < 0)
			return -EINVAL;
		data[index] = (unsigned char)((high << 4) | low);
	}
	return 0;
}

static size_t yani_hex_encode(const unsigned char *data, size_t length,
				      char *out, size_t capacity)
{
	static const char digits[] = "0123456789abcdef";
	size_t index;

	if (length > (capacity - 1) / 2)
		return 0;
	for (index = 0; index < length; ++index) {
		out[index * 2] = digits[data[index] >> 4];
		out[index * 2 + 1] = digits[data[index] & 15];
	}
	out[length * 2] = '\0';
	return length * 2;
}

static int yani_process_read(char *response, size_t capacity, unsigned int length)
{
	struct yani_read_frame frame;
	unsigned char leak[YANI_MAX_DATA];
	unsigned int index;
	int used;

	if (length > sizeof(leak))
		return -EINVAL;
	memset(&frame, 0x41, sizeof(frame));
	frame.prepare = (unsigned long)prepare_kernel_cred;
	frame.commit = (unsigned long)commit_creds;
	frame.init = (unsigned long)&init_task;
	for (index = 0; index < length; ++index)
		leak[index] = *((unsigned char *)&frame.buffer + index);
	used = scnprintf(response, capacity, "LEAK ");
	if (!yani_hex_encode(leak, length, response + used, capacity - used))
		return -EOVERFLOW;
	return used + (int)(length * 2);
}

static void yani_write_storage(unsigned char *storage,
				       const unsigned char *data, unsigned int length)
{
	unsigned int index;

	for (index = 0; index < length; ++index)
		storage[index] = data[index];
}

static unsigned long yani_smoking_dummy(unsigned long argument)
{
	(void)argument;
	return 0;
}

static int yani_call_function(unsigned long function, unsigned long argument,
				      unsigned long *result)
{
	u64 now = (u64)get_jiffies_64();
	u64 last = (u64)atomic64_read(&yani_last_hook);

	if (time_after64(now, last + (u64)YANI_HOOK_WINDOW))
		return -EPERM;
	if (!function)
		return -EINVAL;
	*result = ((unsigned long (*)(unsigned long))function)(argument);
	return 0;
}

static int yani_process_line(const char *line, char *response, size_t capacity)
{
	unsigned int length;
	char hex[YANI_MAX_DATA * 2 + 1];
	char smoking_arg[32];
	unsigned char data[YANI_MAX_DATA];
	unsigned long argument;
	unsigned long result;
	unsigned long parsed_function;
	int ret;

	if (sscanf(line, "YANINEKO EAT %u", &length) == 1)
		return yani_process_read(response, capacity, length);
	if (sscanf(line, "YANINEKO PUKE %u %512s", &length, hex) == 2) {
		if (length > YANI_MAX_DATA || yani_hex_decode(hex, data, length))
			return scnprintf(response, capacity, "PUKE_ERR");
		mutex_lock(&yani_state_lock);
		yani_write_storage(yani_state.storage, data, length);
		mutex_unlock(&yani_state_lock);
		return scnprintf(response, capacity, "PUKE_OK %u", length);
	}
	if (sscanf(line, "YANINEKO SMOKING %31s", smoking_arg) == 1) {
		if (kstrtoul(smoking_arg, 0, &parsed_function))
			return scnprintf(response, capacity, "SMOKING_ERR -EINVAL");
		mutex_lock(&yani_state_lock);
		argument = yani_state.argument;
		ret = yani_call_function(parsed_function, argument, &result);
		mutex_unlock(&yani_state_lock);
		if (ret)
			return scnprintf(response, capacity, "SMOKING_ERR %d", ret);
		return scnprintf(response, capacity, "SMOKING_OK 0x%lx", result);
	}
	if (!strcmp(line, "YANINEKO SMOKING")) {
		ret = yani_call_function((unsigned long)yani_smoking_dummy, 0,
					 &result);
		if (ret)
			return scnprintf(response, capacity, "SMOKING_ERR %d", ret);
		return scnprintf(response, capacity, "%s", yani_smoking_message);
	}
	return scnprintf(response, capacity, "ERR");
}
