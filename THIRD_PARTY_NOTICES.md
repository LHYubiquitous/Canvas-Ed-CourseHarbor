# Third-Party Notices

This project is MIT-licensed and is based on the MIT-licensed upstream
`r1ckyIn/canvas-ed-mcp`. Runtime dependencies are not vendored in the source archive;
they are installed separately under their own licenses. Exact versions, markers,
hashes, and dependency edges are recorded in `pylock.toml`, `uv.lock`, and
`sbom.cdx.json`.

The following license expressions were collected from installed distribution metadata
on Windows/Python 3.13 and official PyPI metadata for OS/Python-conditional packages.
This inventory is informational, not legal advice; the license files shipped inside
each installed distribution remain authoritative.

| License expression/family | Packages |
|---|---|
| MIT / MIT License | annotated-doc, annotated-types, anyio, attrs, backports-tarfile, beautifulsoup4, charset-normalizer, exceptiongroup, fastapi, gradescopeapi, h11, httpx-sse, iniconfig, jaraco.classes, jaraco.context, jaraco.functools, jeepney, jsonschema, jsonschema-specifications, keyring, mcp, more-itertools, platformdirs, pluggy, pydantic, pydantic-core, pydantic-settings, PyJWT, pytest, referencing, rpds-py, six, soupsieve, tomli, tomli-w, typing-inspection, urllib3, zipp |
| MIT-0 | cffi |
| BSD / BSD-3-Clause | click, colorama, httpcore, httpx, idna, pycparser, python-dotenv, pywin32-ctypes, SecretStorage, sse-starlette, starlette, uvicorn |
| BSD-2-Clause | Pygments |
| Apache-2.0 | importlib-metadata, opentelemetry-api, python-multipart, requests, requests-toolbelt, tzdata |
| Apache-2.0 OR BSD-2-Clause | packaging |
| Apache-2.0 OR BSD-3-Clause | cryptography |
| Apache and BSD dual license | python-dateutil |
| MPL-2.0 | certifi |
| PSF-2.0 / Python Software Foundation License | pywin32, typing-extensions |

Optional `gradescopeapi` support is disabled by default and uses an unofficial
scraping integration. External Canvas, Ed, and Gradescope services, and any course
material accessed through them, are not distributed or licensed by this project.
