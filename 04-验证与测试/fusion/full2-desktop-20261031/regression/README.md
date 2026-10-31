# Locked runtime regression

The 18 selected existing test modules passed: 527 passed, zero failed, zero skipped. This is a bounded regression set, not the entire repository or business acceptance. Static checks also passed; see summary.json for exact commands, source identities and exit codes.

Production runtime: Python3.12.16, pypdfium2 5.14.0 / PDFium156.0.8076.0. ReportLab5.0.1 was supplied only to the test container through a temporary offline check-helper overlay; it was absent from the production image and Desktop host venv. No production dependency or security capability was broadened for fixture generation.

The former pypdfium2 4.30.1 release was withdrawn upstream for a PDFium text-extraction regression ([maintainer issue](https://github.com/pypdfium2-team/pypdfium2/issues/336#issuecomment-2565970483)). A tested 4.30.0 rollback lacked the text-object API required by navigation, so it was rejected. The selected [5.14.0 release](https://pypi.org/project/pypdfium2/5.14.0/) was tested with the supported bounds/image APIs and explicit full-Unicode extraction. The versioned provider profile now includes the actual installed inspector version; old qualifications are not inherited merely because the conversion image is unchanged.

The single recorded test container was removed. These files preserve execution bytes; later Git/CI qualification must identify the final source revision separately. Full Desktop installation and Word lifecycle have their own acceptance record.
