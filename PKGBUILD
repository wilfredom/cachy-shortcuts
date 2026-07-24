# Maintainer: (your name here)
#
# Builds directly from this source tree -- clone the repo and run
# `makepkg -si` from its root. There's no upstream tarball/tag yet, so this
# intentionally doesn't fetch anything: `source` is empty and `build()`/
# `package()` both operate on $startdir, the directory this PKGBUILD lives in.

pkgname=cachy-shortcuts
pkgver=0.1.0
pkgrel=1
pkgdesc="An editable, searchable keybinding atlas for COSMIC, Niri and MangoWM"
arch=('any')
url="https://github.com/wilfredom/cachy-shortcuts"
license=('MIT')
depends=(
  'python'
  'python-gobject'
  'gtk4'
  'gtk4-layer-shell'
)
optdepends=(
  'python-yaml: more forgiving parsing of cheat-sheet packs'
  'niri: keybinding backend'
  'mangowc: keybinding backend'
  'cosmic-session: keybinding backend'
)
makedepends=('python-build' 'python-installer' 'python-wheel')
source=()
sha256sums=()

build() {
  cd "$startdir"
  python -m build --wheel --no-isolation --outdir "$srcdir/dist"
}

package() {
  cd "$startdir"
  python -m installer --destdir="$pkgdir" "$srcdir/dist"/*.whl

  install -Dm644 packaging/cachy-shortcuts.desktop \
    "$pkgdir/usr/share/applications/cachy-shortcuts.desktop"
  install -Dm644 README.md \
    "$pkgdir/usr/share/doc/$pkgname/README.md"
}
