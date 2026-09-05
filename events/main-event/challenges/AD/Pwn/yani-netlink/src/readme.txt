YANI-NETLINK

         (  )   (   )
       (      )       )
     (  /\__/\   (  )
      ( o . o )    )
       \  ^  /---~~
      /      \

                 YANINEKO
       +-------------------------------+
       | EAT | PUKE | SMOKING | Mrrph  |
       +-------------------------------+
  YANINEKO EAT <length>
  YANINEKO PUKE <length> <hex>
  YANINEKO SMOKING
  YANINEKO SMOKING <address>
  YANINEKO Mrrph

The service listens on TCP and UDP port 31337.

INTERACTION

Send one newline-terminated command at a time:

  YANINEKO EAT <length>
  YANINEKO PUKE <length> <hex>
  YANINEKO SMOKING
  YANINEKO SMOKING <address>
  YANINEKO Mrrph

TCP sends one response line for each command. UDP returns the banner followed
by the response in the same datagram.

PATCHING

Keep bzImage unchanged. Build your patch against the Linux 7.2.3 source tree:

https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.2.3.tar.xz

The image contains the matching kernel.config and Module.symvers. Use them to
prepare and build your patch without rebuilding the kernel image.

MEMORY BLACKLIST

Do not write outside the behavior you intend to change. Protected kernel memory
is checked by the service. A patch that changes a protected range or removes a
public menu operation fails the checker.

BUILD CONFIGURATION

The exact generated .config is /opt/yani/kernel.config. The fragment used for
the challenge build is:

CONFIG_NETFILTER=y
CONFIG_NETFILTER_ADVANCED=y
CONFIG_INET=y
CONFIG_YANI_NETFILTER=y
CONFIG_DEFAULT_MMAP_MIN_ADDR=0
CONFIG_RANDOMIZE_BASE=y
CONFIG_RANDOMIZE_KSTACK_OFFSET=y
CONFIG_RANDOMIZE_KSTACK_OFFSET_DEFAULT=y
CONFIG_STACKPROTECTOR_STRONG=y
CONFIG_FORTIFY_SOURCE=y
CONFIG_HARDENED_USERCOPY=y
CONFIG_REFCOUNT_FULL=y
CONFIG_VMAP_STACK=y
CONFIG_STRICT_KERNEL_RWX=y
CONFIG_CRYPTO_SHA256=y
CONFIG_MODULES=y
CONFIG_MODULE_UNLOAD=y
CONFIG_MODVERSIONS=n
