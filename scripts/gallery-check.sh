#!/bin/sh
# gallery-check.sh: exercise the Calamares gallery page headless (roadmap 5.6b lot 3).
#   sh scripts/gallery-check.sh
# Unprivileged container from glue-cala-img, QT_QPA_PLATFORM=offscreen, no devices.
# Exercised: GalleryNav.js (wrap next/prev, labels 1/2 and 0/0, tab labels) in qml6;
# gluegallery.qml loaded as a plain Item with the real GalleryData.js (no QML
# errors on stderr, indicator "1/2" on glueqs, Next/Previous wrap, entry switch
# resets to 1/N), plus copies with empty data and a missing image (fallback:
# hasImage false, indicator "0/0"). NOT exercised: running inside Calamares
# (the module loader), and no screenshot is produced, so nothing here is visual.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${GLUE_CALA_IMAGE:-glue-cala-img}
SRC=$REPO/packages/glue-calamares-config/modules/gluegallery
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/real" "$WORK/empty" "$WORK/broken"
for d in real empty broken; do
    cp "$SRC/gluegallery.qml" "$SRC/GalleryNav.js" "$WORK/$d/"
done
cp "$SRC/GalleryData.js" "$WORK/real/"
printf '.pragma library\nvar entries = [];\n' > "$WORK/empty/GalleryData.js"
printf '.pragma library\nvar entries = [{"id":"x","name":"X","kind":"session","images":["file:///nonexistent/a.png","file:///nonexistent/b.png"]}];\n' > "$WORK/broken/GalleryData.js"

cat > "$WORK/nav.qml" <<'QML'
import QtQml
import "real/GalleryNav.js" as Nav
QtObject {
    function eq(name, got, want) {
        if (got === want) console.log("PASS " + name)
        else { console.log("FAIL " + name + ": got " + got + " want " + want); fails++ }
    }
    property int fails: 0
    Component.onCompleted: {
        eq("next wraps", Nav.next(1, 2), 0)
        eq("next steps", Nav.next(0, 2), 1)
        eq("prev wraps", Nav.prev(0, 2), 1)
        eq("prev steps", Nav.prev(1, 2), 0)
        eq("single next", Nav.next(0, 1), 0)
        eq("single prev", Nav.prev(0, 1), 0)
        eq("empty next", Nav.next(0, 0), 0)
        eq("empty prev", Nav.prev(0, 0), 0)
        eq("label 1/2", Nav.label(0, 2), "1/2")
        eq("label 2/2", Nav.label(1, 2), "2/2")
        eq("label 0/0", Nav.label(0, 0), "0/0")
        eq("count none", Nav.imageCount(null), 0)
        eq("tab shell", Nav.tabLabel({name: "glueqs", kind: "shell"}), "glueqs shell")
        eq("tab session", Nav.tabLabel({name: "XFCE", kind: "session"}), "XFCE")
        console.log(fails === 0 ? "NAV-DONE ok" : "NAV-DONE failed")
        Qt.exit(fails === 0 ? 0 : 1)
    }
}
QML

# page.qml template (@DIR@/@SCENARIO@ filled per scenario): loads gluegallery.qml and drives it.
cat > "$WORK/page.qml" <<'QML'
import QtQuick
import QtQuick.Window
Window {
    id: root
    width: 900; height: 600; visible: true
    property string dir: "@DIR@"
    property string scenario: "@SCENARIO@"
    property int fails: 0
    function eq(name, got, want) {
        if (got === want) console.log("PASS " + scenario + ": " + name)
        else { console.log("FAIL " + scenario + ": " + name + ": got " + got + " want " + want); fails++ }
    }
    Loader {
        id: loader
        anchors.fill: parent
        source: "file://" + root.dir + "/gluegallery.qml"
        onStatusChanged: if (status === Loader.Error) { console.log("FAIL load error"); Qt.exit(1) }
    }
    function press(label) {
        // find the Button with this text and click it
        var stack = [loader.item]
        while (stack.length) {
            var it = stack.pop()
            if (it.text === label && typeof it.clicked === "function") { it.clicked(); return true }
            for (var i = 0; i < it.children.length; i++) stack.push(it.children[i])
        }
        return false
    }
    Timer {
        interval: 1500; running: true
        onTriggered: {
            var p = loader.item
            if (!p) { console.log("FAIL " + scenario + ": page not loaded"); Qt.exit(1); return }
            if (scenario === "real") {
                eq("8 entries", p.entry !== null, true)
                eq("starts 1/1 on gluewc", p.indicatorText, "1/1")
                p.entryIndex = 6
                eq("glueqs 1/2", p.indicatorText, "1/2")
                eq("Next pressed", root.press("Next"), true)
                eq("glueqs 2/2", p.indicatorText, "2/2")
                root.press("Next")
                eq("Next wraps to 1/2", p.indicatorText, "1/2")
                root.press("Previous")
                eq("Previous wraps to 2/2", p.indicatorText, "2/2")
                p.entryIndex = 7
                eq("switching entry resets to 1/2", p.indicatorText, "1/2")
            } else {
                eq("hasImage false", p.hasImage, false)
                eq("indicator 0/0", p.indicatorText, "0/0")
            }
            Qt.exit(fails === 0 ? 0 : 1)
        }
    }
}
QML

docker run --rm --network none -v "$WORK:/w" \
    -v "$REPO/packages/glue-installer/catalog:/usr/share/glue-installer/catalog:ro" -e QT_QPA_PLATFORM=offscreen "$IMAGE" sh -c '
set -u
cd /w; rc=0
qml6 nav.qml >nav.out 2>nav.err || rc=1
for s in real empty broken; do
    sed "s|@DIR@|/w/$s|; s|@SCENARIO@|$s|" page.qml > page-$s.qml
    qml6 page-$s.qml >$s.out 2>$s.err || rc=1
done
for f in nav real empty broken; do
    echo "--- $f"; grep -E "^qml: (PASS|FAIL|NAV-DONE)" $f.err; cat $f.out
    # console.log output ("qml: PASS/FAIL ...") also lands on stderr: drop it, and the
    # expected missing-image notice of the "broken" scenario, then anything left is an error
    bad=$(grep -v -E "^qml: (PASS|FAIL|NAV-DONE)" $f.err | grep -v -E "^qt\.qpa" || true)
    [ $f = broken ] && bad=$(echo "$bad" | grep -v "Cannot open: file:///nonexistent/" || true)
    if [ -n "$bad" ]; then echo "FAIL stderr has QML errors:"; echo "$bad" | head -20; rc=1
    else echo "PASS stderr clean (no QML errors, TypeError, unresolved imports)"; fi
done
grep -q "FAIL" nav.err real.err empty.err broken.err && rc=1
exit $rc'
echo "gallery-check: exercised GalleryNav.js logic and gluegallery.qml loading/navigation/fallback in qml6 (offscreen);"
echo "not exercised: Calamares module loader, real rendering (no screenshot taken)."
