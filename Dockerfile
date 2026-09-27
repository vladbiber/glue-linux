# Glue Linux build environment.
# An Artix container with artools + CachyOS repo, used to build the custom
# package repo and then the ISO. Must be run with --privileged (loop devices,
# overlayfs, squashfs) — see build.sh.
FROM artixlinux/artixlinux:latest

# pacman's scriptlet sandbox needs network/namespace isolation that an
# unprivileged `docker build` can't grant (landlock/unshare -> EPERM:
# "could not isolate the network"). Turn it off for the build image; the
# resulting ISO is unaffected.
RUN sed -i '/^\[options\]/a DisableSandbox' /etc/pacman.conf

# base toolchain + artools (buildiso) + makepkg deps, in one coherent upgrade
RUN pacman -Syu --noconfirm --needed \
        base-devel git sudo \
        artools artools-base \
        squashfs-tools dosfstools libisoburn \
        gptfdisk parted \
        perl && \
    pacman -Scc --noconfirm

# CachyOS repo (for linux-cachyos). We configure the repo + trust its key and
# pull ONLY the noarch keyring/mirrorlist onto the host. We deliberately do NOT
# run cachyos-repo.sh — it force-installs CachyOS's own `pacman` build, whose
# libgpgme/libassuan ABI doesn't match the Artix base image and bricks pacman
# (partial upgrade). The kernel is installed into the TARGET by basestrap, where
# the whole transaction is coherent. Fail loudly if the kernel isn't resolvable.
RUN set -eux; \
    pacman-key --init; \
    pacman-key --populate artix; \
    for ks in keyserver.ubuntu.com hkps://keyserver.ubuntu.com pgp.mit.edu; do \
        pacman-key --recv-keys F3B607488DB35A47 --keyserver "$ks" && break; \
    done; \
    pacman-key --lsign-key F3B607488DB35A47; \
    printf '\n[cachyos]\nServer = https://mirror.cachyos.org/repo/$arch/$repo\n' >> /etc/pacman.conf; \
    pacman -Sy --noconfirm cachyos-keyring cachyos-mirrorlist; \
    grep -q '^\[cachyos\]' /etc/pacman.conf; \
    pacman -Sp linux-cachyos >/dev/null

# unprivileged build user (makepkg refuses to run as root)
RUN useradd -m -G wheel builder && \
    echo 'builder ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/builder

WORKDIR /glue
COPY . /glue
RUN chown -R builder:builder /glue

ENTRYPOINT ["/glue/scripts/make-iso.sh"]
