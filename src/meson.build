pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()

install_data(
  [
    'endpaper/__init__.py',
    'endpaper/__main__.py',
    'endpaper/book_info.py',
    'endpaper/config.py',
    'endpaper/epub.py',
    'endpaper/fileio.py',
    'endpaper/book_index.py',
    'endpaper/indexer.py',
    'endpaper/library.py',
    'endpaper/main.py',
    'endpaper/markdown_style.py',
    'endpaper/markdown_view.py',
    'endpaper/notes.py',
    'endpaper/notes_panel.py',
    'endpaper/reader_view.py',
    'endpaper/shelf.py',
    'endpaper/window.py',
  ],
  install_dir: pkgdatadir / 'endpaper',
)

# The reading area: our page, and the rendering library foliate-js
install_subdir('endpaper/web', install_dir: pkgdatadir / 'endpaper')

launcher = configuration_data()
launcher.set('pkgdatadir', pkgdatadir)
launcher.set('localedir', get_option('prefix') / get_option('localedir'))

configure_file(
  input: 'endpaper.in',
  output: 'endpaper',
  configuration: launcher,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)
