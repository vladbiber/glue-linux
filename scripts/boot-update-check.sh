#!/bin/bash
# boot-update-check.sh — probe, in the build container, that the glue-boot
# package keeps /boot/limine.conf and the Limine files current through pacman
# (roadmap 3.3 + 3.5, ADR-022).
#
#   sh scripts/boot-update-check.sh
#
# Host side: one `docker run --rm --privileged --network host` of
# glue-pkgbuild-img with the repo on /glue; exit code non-zero on any FAIL.
# Inside (--inside, root) the probes are:
#   install-pkg         makepkg + pacman -U glue-boot: script + two hooks present
#   initial-conf        dummy kernel + /etc/glue/boot.conf -> limine.conf written
#   deploy-efi          --deploy copies BOOTX64.EFI to EFI/BOOT + EFI/limine,
#                       warns (exit 0) because the container has no EFI variables
#   second-kernel-hook  pacman -U of a fake kernel package -> new entry appears
#   remove-kernel-hook  pacman -R of it -> entry gone, primary kept
#   limine-deploy-hook  reinstalling limine runs the deploy hook, EFI file refreshed
#   idempotent          two runs -> identical limine.conf
#   no-bootconf-noop    without boot.conf: exit 0, limine.conf untouched
#   foreign-entry-survives-kernel-update  boot.d/10-windows-deadbeef.conf stays
#                       in limine.conf, after the kernel entries, across
#                       pacman -U / pacman -R of the fake kernel (3.4)
set -u

IMAGE="${GLUE_CHECK_IMAGE:-glue-pkgbuild-img}"
RESULTS=/tmp/boot-update-check.results
BOOTCONF=/etc/glue/boot.conf
ROOT_UUID=3f1c2a9e-7b4d-4c1e-9a6f-0d2e8b5c1a77

log() { printf '[boot-update-check] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

host_main() {
    local root start rc=0
    root=$(cd -- "$(dirname -- "$0")/.." && pwd)
    command -v docker >/dev/null 2>&1 || die "docker not found"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || die "docker image $IMAGE not found (run ./build.sh first)"
    start=$(date +%s)
    docker run --rm --privileged --network host -v "$root:/glue" -w /glue \
        "$IMAGE" /bin/bash /glue/scripts/boot-update-check.sh --inside || rc=$?
    log "done in $(( $(date +%s) - start )) s (exit $rc)"
    return $rc
}

record() { printf '%-38s %-4s %s\n' "$1" "$2" "$3" >> "$RESULTS"; }
sha() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }

build_and_install() { # build_and_install DIR — makepkg as builder, then pacman -U
    local dir=$1 pkg
    chown -R builder:builder "$dir"
    ( cd "$dir" && sudo -u builder makepkg -f --syncdeps --noconfirm --skippgpcheck ) >"$dir/makepkg.log" 2>&1 \
        || { tail -n 20 "$dir/makepkg.log" >&2; die "makepkg in $dir failed"; }
    pkg=$(ls -1 "$dir"/*.pkg.tar.* | head -n1)
    pacman -U --noconfirm "$pkg"
}

inside_main() {
    local rc=0 out s1 s2 before
    : > "$RESULTS"
    grep -q '^DisableSandbox' /etc/pacman.conf || sed -i '/^\[options\]/a DisableSandbox' /etc/pacman.conf
    log "syncing package databases"
    pacman -Sy --noconfirm >/dev/null
    pacman -S --noconfirm --needed limine >/dev/null 2>&1 || die "limine not installable"
    # never touch the host's NVRAM from here: no efibootmgr, and hide any
    # efivars the container might see (privileged sysfs)
    ! pacman -Q efibootmgr >/dev/null 2>&1 || pacman -Rdd --noconfirm efibootmgr >/dev/null
    if [ -d /sys/firmware/efi/efivars ] && [ -n "$(ls -A /sys/firmware/efi/efivars 2>/dev/null)" ]; then
        mount -t tmpfs none /sys/firmware/efi/efivars || die "cannot hide efivars"
    fi

    # (1) build + install glue-boot
    rm -r -f /tmp/build-glue-boot; cp -r /glue/packages/glue-boot /tmp/build-glue-boot
    rm -f /tmp/build-glue-boot/*.pkg.tar.*
    build_and_install /tmp/build-glue-boot >/dev/null
    if [ -x /usr/bin/glue-boot-update ] && [ -f /usr/share/libalpm/hooks/95-glue-boot-update.hook ] \
        && [ -f /usr/share/libalpm/hooks/95-glue-boot-deploy.hook ]; then
        record install-pkg PASS "script + 2 hooks installed ($(pacman -Q glue-boot))"
    else record install-pkg FAIL "files missing after pacman -U"; fi

    # (2) dummy primary kernel + boot.conf, then --deploy
    mkdir -p /boot /etc/glue
    rm -f /boot/limine.conf; rm -r -f /boot/EFI
    echo dummy > /boot/vmlinuz-linux-cachyos
    echo dummy > /boot/initramfs-linux-cachyos.img
    echo dummy > /boot/initramfs-linux-cachyos-fallback.img
    cat > "$BOOTCONF" <<CONF
GLUE_BOOT_FIRMWARE=uefi
GLUE_BOOT_KERNEL=linux-cachyos
GLUE_BOOT_ROOT_UUID=$ROOT_UUID
GLUE_BOOT_RESUME_UUID=
GLUE_BOOT_CMDLINE="rw quiet loglevel=3 rd.udev.log_level=3 nowatchdog zswap.enabled=0"
GLUE_BOOT_TIMEOUT=5
CONF
    out=$(glue-boot-update --deploy 2>&1); rc=$?
    if [ $rc -eq 0 ] && grep -qx '/Glue Linux' /boot/limine.conf \
        && grep -qx 'kernel_path: boot():/vmlinuz-linux-cachyos' /boot/limine.conf \
        && grep -q "root=UUID=$ROOT_UUID rw quiet" /boot/limine.conf; then
        record initial-conf PASS "limine.conf: $(grep -c '^/Glue Linux' /boot/limine.conf) entries, primary linux-cachyos"
    else record initial-conf FAIL "exit $rc: $out"; fi
    if [ $rc -eq 0 ] && [ -f /boot/EFI/BOOT/BOOTX64.EFI ] && [ -f /boot/EFI/limine/BOOTX64.EFI ] \
        && printf '%s' "$out" | grep -q 'warning: no EFI variables'; then
        record deploy-efi PASS "EFI/BOOT + EFI/limine written, warned without efivars, exit 0"
    else record deploy-efi FAIL "exit $rc: $out"; fi
    rc=0

    # (3) fake kernel package through pacman -U -> 95-glue-boot-update.hook
    rm -r -f /tmp/build-linux-fake; mkdir -p /tmp/build-linux-fake
    cat > /tmp/build-linux-fake/PKGBUILD <<'PKG'
pkgname=linux-fake
pkgver=6.0.0
pkgrel=1
pkgdesc="fake kernel for the glue-boot hook test"
arch=('any')
license=('GPL-2.0-only')
options=('!strip')
package() {
    install -d "$pkgdir/usr/lib/modules/6.0.0-fake" "$pkgdir/boot"
    echo linux-fake > "$pkgdir/usr/lib/modules/6.0.0-fake/pkgbase"
    echo dummy > "$pkgdir/usr/lib/modules/6.0.0-fake/vmlinuz"
    echo dummy > "$pkgdir/boot/vmlinuz-linux-fake"
    echo dummy > "$pkgdir/boot/initramfs-linux-fake.img"
}
PKG
    out=$(build_and_install /tmp/build-linux-fake 2>&1) || true
    if printf '%s' "$out" | grep -q 'Updating Limine boot entries' \
        && grep -qx '/Glue Linux (linux-fake)' /boot/limine.conf \
        && grep -qx 'kernel_path: boot():/vmlinuz-linux-fake' /boot/limine.conf \
        && grep -qx '/Glue Linux' /boot/limine.conf; then
        record second-kernel-hook PASS "pacman -U linux-fake ran the hook; entry added"
    else record second-kernel-hook FAIL "$(printf '%s' "$out" | tail -n 5 | tr '\n' '|')"; fi

    # (4) remove it -> entry gone
    out=$(pacman -R --noconfirm linux-fake 2>&1) || true
    if ! grep -q 'linux-fake' /boot/limine.conf && grep -qx '/Glue Linux' /boot/limine.conf \
        && grep -qx 'kernel_path: boot():/vmlinuz-linux-cachyos' /boot/limine.conf; then
        record remove-kernel-hook PASS "pacman -R linux-fake: entry gone, primary kept"
    else record remove-kernel-hook FAIL "$(printf '%s' "$out" | tail -n 5 | tr '\n' '|')"; fi

    # (5) reinstall limine -> 95-glue-boot-deploy.hook
    echo stale > /boot/EFI/limine/BOOTX64.EFI
    out=$(pacman -S --noconfirm limine 2>&1) || true
    if printf '%s' "$out" | grep -q 'Deploying Limine' \
        && [ "$(sha /boot/EFI/limine/BOOTX64.EFI)" = "$(sha /usr/share/limine/BOOTX64.EFI)" ] \
        && [ "$(sha /boot/EFI/BOOT/BOOTX64.EFI)" = "$(sha /usr/share/limine/BOOTX64.EFI)" ]; then
        record limine-deploy-hook PASS "hook ran on reinstall; BOOTX64.EFI sha256 matches /usr/share/limine"
    else record limine-deploy-hook FAIL "$(printf '%s' "$out" | tail -n 5 | tr '\n' '|')"; fi

    # (6) idempotent
    glue-boot-update >/dev/null 2>&1; s1=$(sha /boot/limine.conf)
    glue-boot-update >/dev/null 2>&1; s2=$(sha /boot/limine.conf)
    if [ -n "$s1" ] && [ "$s1" = "$s2" ] && [ ! -e /boot/limine.conf.new ]; then
        record idempotent PASS "sha256 ${s1:0:16}… twice, no .new left"
    else record idempotent FAIL "$s1 vs $s2"; fi

    # (7) no boot.conf -> noop
    before=$(sha /boot/limine.conf)
    mv "$BOOTCONF" "$BOOTCONF.off"
    out=$(glue-boot-update 2>&1); rc=$?
    mv "$BOOTCONF.off" "$BOOTCONF"
    if [ $rc -eq 0 ] && [ "$(sha /boot/limine.conf)" = "$before" ] && printf '%s' "$out" | grep -q 'nothing to do'; then
        record no-bootconf-noop PASS "exit 0, limine.conf unchanged"
    else record no-bootconf-noop FAIL "exit $rc: $out"; fi

    # (8) boot.d entry survives a kernel install + removal (roadmap 3.4)
    mkdir -p /etc/glue/boot.d
    printf '# Windows Boot Manager on /dev/sdz1 (os-prober)\n/Windows Boot Manager\nprotocol: efi\nimage_path: guid(deadbeef-1111-2222-3333-444444444444):/EFI/Microsoft/Boot/bootmgfw.efi\n' \
        > /etc/glue/boot.d/10-windows-deadbeef.conf
    pkg=$(ls -1 /tmp/build-linux-fake/*.pkg.tar.* | head -n1)
    out=$(pacman -U --noconfirm "$pkg" 2>&1) || true
    w1=$(grep -n -x '/Windows Boot Manager' /boot/limine.conf | cut -d: -f1)
    k1=$(grep -n -x '/Glue Linux (linux-fake)' /boot/limine.conf | cut -d: -f1)
    out=$(pacman -R --noconfirm linux-fake 2>&1) || true
    w2=$(grep -n -x '/Windows Boot Manager' /boot/limine.conf | cut -d: -f1)
    if [ -n "$w1" ] && [ -n "$k1" ] && [ "$k1" -lt "$w1" ] && [ -n "$w2" ] \
        && ! grep -q linux-fake /boot/limine.conf \
        && grep -qx 'image_path: guid(deadbeef-1111-2222-3333-444444444444):/EFI/Microsoft/Boot/bootmgfw.efi' /boot/limine.conf; then
        record foreign-entry-survives-kernel-update PASS "Windows entry kept after pacman -U/-R, line $w1 > kernel line $k1"
    else record foreign-entry-survives-kernel-update FAIL "w1=$w1 k1=$k1 w2=$w2"; fi
    rm -f /etc/glue/boot.d/10-windows-deadbeef.conf

    printf '\n== glue-boot: pacman hook probes ==\n%s\n' "$(cat "$RESULTS")"
    printf '\n== /boot/limine.conf ==\n'; cat /boot/limine.conf
    rc=0
    ! grep -q ' FAIL ' "$RESULTS" || rc=1
    [ "$(grep -c -E ' (PASS|FAIL) ' "$RESULTS")" -ge 9 ] || { log "fewer than 9 probes recorded"; rc=1; }
    return $rc
}

case ${1:-} in
    --inside) inside_main ;;
    *) host_main ;;
esac
