#!/bin/bash
# usage: run.sh NAME user@host [BENCH=1]
# Password (login + sudo) is read from ~/.config/gluebench/pass, never from argv.
set -eu
name=$1 host=$2; shift 2
dir=$(cd "$(dirname "$0")" && pwd)
cfg=$HOME/.config/gluebench
pass=$cfg/pass_$name; [ -r "$pass" ] || pass=$cfg/pass
[ -r "$pass" ] || { echo "missing $pass" >&2; exit 1; }
out=$HOME/.cache/gluebench/$name-$(date +%Y%m%d-%H%M%S)
mkdir -p "$out"

askpass=$(mktemp); trap 'rm -f "$askpass"' EXIT
printf '#!/bin/sh\nhead -n1 %q\n' "$pass" >"$askpass"; chmod 700 "$askpass"

# one known_hosts per OS: both boots share an IP but have different host keys
SSH_ASKPASS=$askpass SSH_ASKPASS_REQUIRE=force \
ssh -o UserKnownHostsFile="$cfg/known_hosts_$name" -o StrictHostKeyChecking=accept-new \
	"$host" "sudo -S -p '' env $* bash -s" \
	< <(head -n1 "$pass"; cat "$dir/collect.sh") | tar -C "$out" -xzf -

echo "$out"
cat "$out/summary.txt"
