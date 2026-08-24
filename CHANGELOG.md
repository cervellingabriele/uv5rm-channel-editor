# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [1.0.0]

### Added
- Initial release.
- `.xlc` reader/writer engine (`xlc_format`) with byte-identical round-trip.
- Tkinter GUI (`editor`): open/save, channel add/edit/duplicate/delete,
  block generator, zone renaming, aircraft-band toggle, automatic backups.
- Unit tests covering frequency/tone BCD, flags, zone tables, overflow and
  band-limit handling.
