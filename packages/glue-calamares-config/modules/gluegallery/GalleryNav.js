.pragma library
// Pure navigation helpers for gluegallery.qml (no QML types, so a headless
// run can exercise them). Navigation WRAPS: Next on the last image goes to the
// first one, Previous on the first goes to the last.

function next(index, count) {
    if (count <= 0) return 0
    return (index + 1) % count
}

function prev(index, count) {
    if (count <= 0) return 0
    return (index - 1 + count) % count
}

// Indicator text "N/M" (1-based); an empty gallery is "0/0".
function label(index, count) {
    if (count <= 0) return "0/0"
    return (index + 1) + "/" + count
}

function imageCount(entry) {
    return (entry && entry.images) ? entry.images.length : 0
}

// Tab text: sessions by name, shells marked so "glueqs" (shell) is not
// confused with the gluewc session that uses it.
function tabLabel(entry) {
    return entry.kind === "shell" ? entry.name + " shell" : entry.name
}
