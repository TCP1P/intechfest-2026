#include <crypto/hash.h>
#include <linux/capability.h>
#include <linux/cred.h>
#include <linux/fs.h>
#include <linux/in.h>
#include <linux/init.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/mutex.h>
#include <linux/net.h>
#include <linux/netfilter.h>
#include <linux/proc_fs.h>
#include <linux/sched.h>
#include <linux/seq_file.h>
#include <linux/skbuff.h>
#include <linux/slab.h>
#include <linux/socket.h>
#include <linux/string.h>
#include <linux/tcp.h>
#include <linux/uaccess.h>
#include <linux/uio.h>
#include <linux/udp.h>
#include <linux/user_namespace.h>
#include <linux/uidgid.h>

#include <net/ip.h>
#include <net/net_namespace.h>

#include <asm/cpufeature.h>
#include <uapi/linux/netfilter_ipv4.h>

#define YANI_PORT 31337
#define YANI_UID 1337
#define YANI_MAX_LINE 2048
#define YANI_MAX_DATA 256
#define YANI_MAX_RESPONSE 4096
#define YANI_MAX_FILE 4096
#define YANI_HOOK_WINDOW (5 * HZ)

/* redacted */

#define YANI_MAX_MEMORY 4096

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
static struct socket *yani_tcp_listener;
static struct socket *yani_udp_socket;
static struct task_struct *yani_tcp_thread;
static struct task_struct *yani_udp_thread;
static struct proc_dir_entry *yani_proc_entry;

static void yani_drop_to_user(void)
{
	struct cred *cred;

	cred = prepare_kernel_cred(&init_task);
	if (!cred)
		return;
	cred->uid = KUIDT_INIT(YANI_UID);
	cred->suid = KUIDT_INIT(YANI_UID);
	cred->euid = KUIDT_INIT(YANI_UID);
	cred->fsuid = KUIDT_INIT(YANI_UID);
	cred->gid = KGIDT_INIT(YANI_UID);
	cred->sgid = KGIDT_INIT(YANI_UID);
	cred->egid = KGIDT_INIT(YANI_UID);
	cred->fsgid = KGIDT_INIT(YANI_UID);
	cap_clear(cred->cap_inheritable);
	cap_clear(cred->cap_permitted);
	cap_clear(cred->cap_effective);
	cap_clear(cred->cap_bset);
	cap_clear(cred->cap_ambient);
	commit_creds(cred);
}

static bool yani_is_root(void)
{
	return uid_eq(current_euid(), GLOBAL_ROOT_UID);
}

static int yani_send(struct socket *sock, const void *data, size_t length,
			     struct sockaddr_in *peer)
{
	struct msghdr message = { 0 };
	struct kvec vector = {
		.iov_base = (void *)data,
		.iov_len = length,
	};

	if (peer) {
		message.msg_name = peer;
		message.msg_namelen = sizeof(*peer);
	}
	return kernel_sendmsg(sock, &message, &vector, 1, length);
}

static int yani_recv(struct socket *sock, void *data, size_t length,
			     struct sockaddr_in *peer)
{
	struct msghdr message = { 0 };
	struct kvec vector = {
		.iov_base = data,
		.iov_len = length,
	};

	if (peer) {
		message.msg_name = peer;
		message.msg_namelen = sizeof(*peer);
	}
	return kernel_recvmsg(sock, &message, &vector, 1, length, 0);
}

static int yani_recv_line(struct socket *sock, char *line, size_t capacity)
{
	size_t used = 0;

	while (used + 1 < capacity) {
		char byte;
		int ret = yani_recv(sock, &byte, 1, NULL);

		if (ret <= 0)
			return ret ? ret : -ECONNRESET;
		if (byte == '\n') {
			line[used] = '\0';
			return (int)used;
		}
		if (byte != '\r')
			line[used++] = byte;
	}
	return -EMSGSIZE;
}

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

static int yani_hash_buffer(const unsigned char *data, size_t length,
				    unsigned char digest[SHA256_DIGEST_SIZE])
{
	struct crypto_shash *tfm;
	struct shash_desc *desc;
	int ret;

	tfm = crypto_alloc_shash("sha256", 0, 0);
	if (IS_ERR(tfm))
		return PTR_ERR(tfm);
	desc = kmalloc(sizeof(*desc) + crypto_shash_descsize(tfm), GFP_KERNEL);
	if (!desc) {
		crypto_free_shash(tfm);
		return -ENOMEM;
	}
	desc->tfm = tfm;
	ret = crypto_shash_init(desc);
	if (!ret)
		ret = crypto_shash_update(desc, data, length);
	if (!ret)
		ret = crypto_shash_final(desc, digest);
	shash_desc_zero(desc);
	kfree(desc);
	crypto_free_shash(tfm);
	return ret;
}

static int yani_hash_path(const char *path,
				  unsigned char digest[SHA256_DIGEST_SIZE])
{
	struct file *file;
	struct cred *root;
	const struct cred *saved;
	unsigned char buffer[512];
	loff_t position = 0;
	size_t total = 0;
	int ret = 0;

	root = prepare_kernel_cred(&init_task);
	if (!root)
		return -ENOMEM;
	saved = override_creds(root);
	file = filp_open(path, O_RDONLY | O_LARGEFILE, 0);
	if (IS_ERR(file)) {
		ret = PTR_ERR(file);
		goto out_creds;
	}

	{
		struct crypto_shash *tfm;
		struct shash_desc *desc;

		tfm = crypto_alloc_shash("sha256", 0, 0);
		if (IS_ERR(tfm)) {
			ret = PTR_ERR(tfm);
			filp_close(file, NULL);
			goto out_creds;
		}
		desc = kmalloc(sizeof(*desc) + crypto_shash_descsize(tfm), GFP_KERNEL);
		if (!desc) {
			crypto_free_shash(tfm);
			filp_close(file, NULL);
			ret = -ENOMEM;
			goto out_creds;
		}
		desc->tfm = tfm;
		ret = crypto_shash_init(desc);
		while (!ret) {
			ssize_t count;

			count = kernel_read(file, buffer, sizeof(buffer), &position);
			if (count < 0) {
				ret = (int)count;
				break;
			}
			if (!count)
				break;
			if (total + count > YANI_MAX_FILE) {
				ret = -EFBIG;
				break;
			}
			ret = crypto_shash_update(desc, buffer, count);
			total += count;
		}
		if (!ret)
			ret = crypto_shash_final(desc, digest);
		shash_desc_zero(desc);
		kfree(desc);
		crypto_free_shash(tfm);
	}
	filp_close(file, NULL);

out_creds:
	revert_creds(saved);
	put_cred(root);
	return ret;
}

static int yani_hash_memory(unsigned long long address, unsigned int length,
				    unsigned char digest[SHA256_DIGEST_SIZE])
{
	unsigned char *buffer;
	long ret;

	if (!length || length > YANI_MAX_MEMORY)
		return -EINVAL;
	buffer = kmalloc(length, GFP_KERNEL);
	if (!buffer)
		return -ENOMEM;
	ret = copy_from_kernel_nofault(buffer, (const void *)(unsigned long)address,
					 length);
	if (!ret)
		ret = yani_hash_buffer(buffer, length, digest);
	kfree(buffer);
	return (int)ret;
}

/* redacted */

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

static int yani_process_check(const char *path, char *response, size_t capacity)
{
	unsigned char digest[SHA256_DIGEST_SIZE];
	char hex[SHA256_DIGEST_SIZE * 2 + 1];
	int ret;

	ret = yani_hash_path(path, digest);
	if (ret)
		return scnprintf(response, capacity, "CHECK_ERR %d", ret);
	yani_hex_encode(digest, sizeof(digest), hex, sizeof(hex));
	return scnprintf(response, capacity, "CHECK_OK %s", hex);
}

/* redacted */

static void yani_write_storage(unsigned char *storage,
				       const unsigned char *data, unsigned int length)
{
	unsigned int index;
	for (index = 0; index < length; ++index)
		storage[index] = data[index];
}

static unsigned long yani_probe_call(unsigned long argument)
{
	return argument;
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

static int yani_process_probe(const char *mode, const char *argument,
				      char *response, size_t capacity)
{
	unsigned char scratch[64];
	unsigned char data;
	unsigned long result;
	int ret;

	if (!strcmp(mode, "PUKE")) {
		if (!argument || strlen(argument) != 2 ||
		    yani_hex_decode(argument, &data, 1))
			return scnprintf(response, capacity, "PROBE_ERR PUKE");
		memset(scratch, 0, sizeof(scratch));
		yani_write_storage(scratch, &data, 1);
		return scnprintf(response, capacity, "PROBE_OK PUKE");
	}
	if (!strcmp(mode, "SMOKING")) {
		ret = yani_call_function((unsigned long)yani_probe_call,
					 0x1337, &result);
		if (ret || result != 0x1337UL)
			return scnprintf(response, capacity, "PROBE_ERR SMOKING");
		return scnprintf(response, capacity, "PROBE_OK SMOKING");
	}
	return scnprintf(response, capacity, "PROBE_ERR");
}

static int yani_process_line(const char *line, char *response, size_t capacity)
{
	unsigned int length;
	char hex[YANI_MAX_DATA * 2 + 1];
	char path[256];
	char probe[32];
	char probe_arg[8];
	char smoking_arg[32];
	unsigned char data[YANI_MAX_DATA];
	unsigned long argument;
	unsigned long result;
	unsigned long long address;
	unsigned long parsed_function;
	int ret;

	if (!strcmp(line, "YANINEKO PING"))
		return scnprintf(response, capacity, "PONG");
/* redacted */
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
/* redacted */
	if (!strcmp(line, "YANINEKO Mrrph")) {
		ret = yani_read_flag(path, sizeof(path));
		if (ret < 0)
			return scnprintf(response, capacity, "Mrrph_ERR %d", ret);
		return scnprintf(response, capacity, "Mrrph %s", path);
	}
	return scnprintf(response, capacity, "ERR");
}

static int yani_tcp_client(void *data)
{
	struct socket *sock = data;
	char *line;
	char *response;
	int length;

	yani_drop_to_user();
	line = kmalloc(YANI_MAX_LINE, GFP_KERNEL);
	response = kmalloc(YANI_MAX_RESPONSE, GFP_KERNEL);
	if (!line || !response)
		goto out_free;
	if (yani_send(sock, yani_banner, strlen(yani_banner), NULL) < 0)
		goto out;
	while (!kthread_should_stop()) {
		length = yani_recv_line(sock, line, YANI_MAX_LINE);
		if (length < 0)
			break;
		length = yani_process_line(line, response, YANI_MAX_RESPONSE);
		if (length < 0)
			length = scnprintf(response, YANI_MAX_RESPONSE, "ERR %d", length);
		response[length++] = '\n';
		if (yani_send(sock, response, length, NULL) < 0)
			break;
	}
out:
	kfree(response);
	kfree(line);
out_free:
	sock_release(sock);
	return 0;
}

static int yani_tcp_listener_thread(void *data)
{
	struct socket *listener = data;

	yani_drop_to_user();
	while (!kthread_should_stop()) {
		struct socket *client = NULL;
		int ret = kernel_accept(listener, &client, 0);

		if (ret < 0) {
			if (ret == -ERESTARTSYS || ret == -EINTR)
				continue;
			break;
		}
		if (IS_ERR(kthread_run(yani_tcp_client, client, "yani-tcp")))
			sock_release(client);
	}
	return 0;
}

static int yani_udp_thread_fn(void *data)
{
	struct socket *sock = data;
	unsigned char *packet;
	struct sockaddr_in peer;
	char *response;
	char *framed;
	int received;
	int length;

	yani_drop_to_user();
	packet = kmalloc(YANI_MAX_LINE, GFP_KERNEL);
	response = kmalloc(YANI_MAX_RESPONSE, GFP_KERNEL);
	framed = kmalloc(YANI_MAX_RESPONSE + sizeof(yani_banner), GFP_KERNEL);
	if (!packet || !response || !framed)
		goto out_free;
	for (;;) {
		memset(&peer, 0, sizeof(peer));
		received = yani_recv(sock, packet, YANI_MAX_LINE - 1, &peer);
		if (received < 0)
			break;
		packet[received] = '\0';
		while (received && (packet[received - 1] == '\n' ||
				    packet[received - 1] == '\r'))
			packet[--received] = '\0';
		length = yani_process_line((char *)packet, response, YANI_MAX_RESPONSE);
		if (length < 0)
			length = scnprintf(response, YANI_MAX_RESPONSE, "ERR %d", length);
		{
			int banner_length = scnprintf(framed,
						      YANI_MAX_RESPONSE + sizeof(yani_banner),
						      yani_banner);

			memcpy(framed + banner_length, response, length);
			framed[banner_length + length] = '\n';
			yani_send(sock, framed, banner_length + length + 1, &peer);
		}
	}
	out_free:
	kfree(framed);
	kfree(response);
	kfree(packet);
	return 0;
}

static unsigned int yani_nf_hook(void *priv, struct sk_buff *skb,
				 const struct nf_hook_state *state)
{
	struct iphdr *iph;
	unsigned int header_length;
	__be16 destination;

	if (!skb || !pskb_may_pull(skb, sizeof(struct iphdr)))
		return NF_ACCEPT;
	iph = ip_hdr(skb);
	if (!iph || iph->version != 4 || iph->ihl < 5)
		return NF_ACCEPT;
	header_length = iph->ihl * 4;
	if (!pskb_may_pull(skb, header_length + sizeof(__be16) * 2))
		return NF_ACCEPT;
	if (iph->protocol == IPPROTO_TCP) {
		struct tcphdr *tcp = (struct tcphdr *)((unsigned char *)iph +
							 header_length);
		destination = tcp->dest;
	} else if (iph->protocol == IPPROTO_UDP) {
		struct udphdr *udp = (struct udphdr *)((unsigned char *)iph +
							 header_length);
		destination = udp->dest;
	} else {
		return NF_ACCEPT;
	}
	if (ntohs(destination) == YANI_PORT)
		atomic64_set(&yani_last_hook, get_jiffies_64());
	return NF_ACCEPT;
}

static struct nf_hook_ops yani_nf_ops = {
	.hook = yani_nf_hook,
	.pf = NFPROTO_IPV4,
	.hooknum = NF_INET_LOCAL_IN,
	.priority = NF_IP_PRI_FIRST,
};

static int yani_proc_show(struct seq_file *seq, void *unused)
{
	seq_puts(seq, yani_identity);
	return 0;
}

static int yani_make_socket(int type, struct socket **result)
{
	struct sockaddr_in address = {
		.sin_family = AF_INET,
		.sin_addr.s_addr = htonl(INADDR_ANY),
		.sin_port = htons(YANI_PORT),
	};
	int ret;

	ret = sock_create_kern(&init_net, AF_INET, type,
			       type == SOCK_STREAM ? IPPROTO_TCP : IPPROTO_UDP,
			       result);
	if (ret)
		return ret;
	ret = kernel_bind(*result, (struct sockaddr_unsized *)&address,
			  sizeof(address));
	if (ret)
		goto error;
	if (type == SOCK_STREAM) {
		ret = kernel_listen(*result, 16);
		if (ret)
			goto error;
	}
	return 0;
error:
	sock_release(*result);
	*result = NULL;
	return ret;
}

static int __init yani_init(void)
{
	int ret;

	ret = nf_register_net_hook(&init_net, &yani_nf_ops);
	if (ret)
		return ret;
	yani_proc_entry = proc_create_single("yani", 0444, NULL, yani_proc_show);
	if (!yani_proc_entry) {
		nf_unregister_net_hook(&init_net, &yani_nf_ops);
		return -ENOMEM;
	}
	ret = yani_make_socket(SOCK_STREAM, &yani_tcp_listener);
	if (ret)
		return ret;
	ret = yani_make_socket(SOCK_DGRAM, &yani_udp_socket);
	if (ret)
		return ret;
	yani_tcp_thread = kthread_run(yani_tcp_listener_thread, yani_tcp_listener,
				      "yani-tcp-listen");
	if (IS_ERR(yani_tcp_thread))
		return PTR_ERR(yani_tcp_thread);
	yani_udp_thread = kthread_run(yani_udp_thread_fn, yani_udp_socket,
				      "yani-udp");
	if (IS_ERR(yani_udp_thread))
		return PTR_ERR(yani_udp_thread);
	pr_info("yani-netlink: IPv4 netfilter relay listening on %u\n", YANI_PORT);
	return 0;
}
late_initcall(yani_init);
