# Test fixtures

## `legacy_policy.doc` / `legacy_policy.converted.docx`

The pair `test_conversion_preserves_heading_structure` reads. Regenerating them
by hand — writing OLE2 bytes, or renaming a `.docx` — would make that test pass
while asserting nothing, since the only thing it is there to measure is whether
a real LibreOffice conversion keeps heading styles. Both files are therefore
produced by the converter image itself, and there is no host LibreOffice in this
project's toolchain.

`legacy_policy.doc` is soffice's own `MS Word 97` export of a python-docx source
carrying `Heading 1` / body / `Heading 2` / body. `legacy_policy.converted.docx`
is what `convert_bytes` — the production argv, pinned `--infilter`, and the
output sanitization — returned for those bytes. Recorded provenance:
LibreOffice 7.4.7.2 40(Build:2), the series `converter.Dockerfile` asserts.

Neither half of the pair is trusted to this prose. `sniff_document_format` is
asserted over the `.doc`, and the `.docx`'s `docProps/app.xml` must still name
the pinned `7.4.` series — so a hand-authored substitute, or a regeneration off
a different LibreOffice, fails the suite instead of passing it vacuously.

To regenerate, with the image built from `backend/converter.Dockerfile`:

```bash
docker run -d --name rmfconv --entrypoint sleep rmf-converter:test infinity
docker exec rmfconv mkdir -p /tmp/w
docker cp source.docx rmfconv:/tmp/w/source.docx        # Heading 1, body, Heading 2, body
docker exec -w /tmp/w rmfconv /usr/bin/soffice --headless \
    --convert-to 'doc:MS Word 97' --outdir /tmp/w /tmp/w/source.docx
docker exec -w /var/task rmfconv /usr/local/bin/python -c \
    "import pathlib; from rmf_migrator.converter_lambda.handler import convert_bytes; \
     pathlib.Path('/tmp/w/converted.docx').write_bytes(convert_bytes(pathlib.Path('/tmp/w/source.doc').read_bytes()))"
docker cp rmfconv:/tmp/w/source.doc legacy_policy.doc
docker cp rmfconv:/tmp/w/converted.docx legacy_policy.converted.docx
```

The headings and body text are asserted by name in `test_convert_ingest.py`, so
a regenerated source has to use the same strings or the test moves with it.
