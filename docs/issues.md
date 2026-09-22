## Issues and Gaps

### Build Time

A full cold build could take over 40 minutes to complete. Next builds will be warm, and will take around 2 minutes.
The release windows/linux builds have no way around that at the moment.

### Authored Glyphs

Some glyphs are missing, and the existing ones could use some polishing.

### Translation Limits and Blockers

A scene has a limited amount of space. Going past it causes a build error,
or a freeze in game. Long lines add to it, but the main cost is the number
of _different_ accented letters a scene uses.

Scenes that go beyond their original byte allocations are registered
[here](../data/record-limits.csv). Scenes that are "candidates" must
be tested in game for any crashes or weird behaviors. Rethinking
sentences to avoid special letters is the best way to fix a broken
scene. A single accented letter can be difference between a scene working
perfectly or crashing instantly.
