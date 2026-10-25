# CourseHarbor application icon

`courseharbor-source.png` was generated for this project with OpenAI's built-in image
generation tool on 2026-10-26. It is an original text-free mark combining an open book,
a protective harbor/shelter, and water, using the existing CourseHarbor navy, blue, and
amber application palette. It intentionally contains no Canvas, Ed, or university mark.

Regenerate the checked-in runtime PNG, Windows multi-resolution ICO, and macOS iconset
from the master image with:

```text
python packaging/icons/generate_icons.py
```

The generator requires the project's PySide6 GUI dependency. On macOS, the reviewed
iconset is converted to ICNS with the native `iconutil` tool during the DMG build.
