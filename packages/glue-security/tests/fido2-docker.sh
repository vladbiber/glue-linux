#!/bin/bash
# Real-cryptsetup check of glue-fido2 + its initcpio hook against the stock
# encrypt hook, on a LUKS2 loop file, inside a privileged Artix container:
#   docker run --rm --privileged -v "$PWD":/gs artixlinux/artixlinux:latest bash /gs/tests/fido2-docker.sh
# FIDO2 tools are stubs (tests/fido2-stubs): no real key is involved. The
# build host kernel may lack dm-crypt, so during the boot runs a cryptsetup
# shim checks each key against the real LUKS2 header (open --test-passphrase)
# and creates /dev/mapper/NAME itself.
set -eu
pacman -Sy --noconfirm --needed cryptsetup mkinitcpio mkinitcpio-busybox >/dev/null 2>&1
export PATH=/gs/tests/fido2-stubs:$PATH
ASH="/usr/lib/initcpio/busybox ash"
W=$(mktemp -d)
fail() { echo "FAIL: $*"; exit 1; }
truncate -s 40M "$W/disk.img"
LOOP=$(losetup -f --show "$W/disk.img")
trap 'rm -f /dev/mapper/testroot; losetup -d "$LOOP"' EXIT
mkdir -p "$W/shim"
cat > "$W/shim/cryptsetup" <<'EOS'
#!/bin/bash
for a; do [ "$a" = open ] && break; done
[ "$a" = open ] || exec /usr/bin/cryptsetup "$@"
name=${!#}
args=()
for a in "${@:1:$#-1}"; do
    [ -n "$a" ] || continue
    args+=("$a")
    [ "$a" = open ] && args+=(--test-passphrase)
done
/usr/bin/cryptsetup "${args[@]}" || exit $?
mknod "/dev/mapper/$name" b 7 250
EOS
chmod +x "$W/shim/cryptsetup"
close() { rm -f /dev/mapper/testroot; }
printf pass | cryptsetup luksFormat -q --type luks2 --pbkdf pbkdf2 --pbkdf-force-iterations 1000 "$LOOP" -
printf 'HOOKS=(base udev autodetect keyboard keymap block encrypt filesystems fsck)\n' > "$W/mkinitcpio.conf"
export GLUE_FIDO2_DIR=$W/fido2 GLUE_MKINITCPIO_CONF=$W/mkinitcpio.conf \
    GLUE_FIDO2_LUKS_DEV=$LOOP GLUE_FIDO2_NO_INITRAMFS=1

echo "== enroll"
printf pass | bash -c 'sh /gs/glue-fido2 enroll'
conf=$(ls "$W"/fido2/key-*.conf) || fail "no key conf"
slot=${conf##*key-}; slot=${slot%.conf}
[ "$slot" = 1 ] || fail "expected keyslot 1, got $slot"
grep -q 'encrypt glue-fido2\|glue-fido2 encrypt' "$W/mkinitcpio.conf" || fail "hook not added"
grep -q ' block glue-fido2 encrypt ' "$W/mkinitcpio.conf" || fail "hook not right before encrypt"
sh /gs/glue-fido2 status | grep -q "keyslot 1" || fail "status"

# the stock encrypt hook, with the helpers init provides
cp /usr/lib/initcpio/hooks/encrypt "$W/encrypt"
cat > "$W/run.sh" <<EOS
getarg() { :; }
resolve_device() { echo "$LOOP"; }
err() { echo "ERR: \$*"; }
msg() { echo "\$*"; }
. /gs/initcpio/hooks/glue-fido2
run_hook
unset -f run_hook
. "$W/encrypt"
cryptdevice="UUID=whatever:testroot"
run_hook
EOS
export GLUE_FIDO2_KEYFILE=/crypto_keyfile.bin
boot() { PATH=$W/shim:$PATH timeout 60 $ASH "$W/run.sh"; }

echo "== boot with the key: no passphrase asked"
out=$(boot < /dev/null 2>&1) || fail "hooks failed: $out"
echo "$out"
[ -b /dev/mapper/testroot ] || fail "mapping not opened by the key"
echo "$out" | grep -q "password is required" && fail "passphrase was asked"
[ ! -e /crypto_keyfile.bin ] || fail "keyfile left in the initramfs"
close

echo "== boot with a wrong key answer: falls back to the passphrase"
out=$(printf 'pass\n' | FIDO_WRONG=1 boot 2>&1) || fail "fallback failed: $out"
echo "$out"
echo "$out" | grep -q "Invalid keyfile. Reverting to passphrase." || fail "no fallback message"
[ -b /dev/mapper/testroot ] || fail "passphrase fallback did not open"
close

echo "== boot without the key: plain passphrase, no keyfile"
out=$(printf 'pass\n' | FIDO_NOKEY=1 boot 2>&1) || fail "no-key boot failed: $out"
echo "$out" | grep -q "Touch your security key" && fail "asked for a touch without a key"
echo "$out" | grep -q "password is required" || fail "no passphrase prompt"
[ -b /dev/mapper/testroot ] || fail "no-key passphrase did not open"
close

echo "== key refusing (timeout/PIN wrong): plain passphrase"
out=$(printf 'pass\n' | FIDO_FAIL=1 boot 2>&1) || fail "refusing key boot failed"
echo "$out" | grep -q "Invalid keyfile" && fail "a keyfile was written on failure"
[ -b /dev/mapper/testroot ] || fail "refusing-key passphrase did not open"
close

echo "== already-open mapping: encrypt does not loop"
mknod /dev/mapper/testroot b 7 250
out=$(boot < /dev/null 2>&1)
echo "$out" | grep -q "already exists, not doing any crypt setup" || fail "encrypt re-prompted on an open mapping"
close

echo "== remove"
printf pass | sh /gs/glue-fido2 remove 1
[ ! -e "$conf" ] || fail "conf left"
grep -q glue-fido2 "$W/mkinitcpio.conf" && fail "hook left in HOOKS"
grep -q '^HOOKS=(base udev autodetect keyboard keymap block encrypt filesystems fsck)$' "$W/mkinitcpio.conf" || fail "HOOKS line changed: $(cat "$W/mkinitcpio.conf")"
cryptsetup luksDump "$LOOP" | grep -q '^  1: luks2' && fail "keyslot 1 still there"
printf pass | cryptsetup open --test-passphrase "$LOOP" - || fail "passphrase lost"
echo "ALL OK"
