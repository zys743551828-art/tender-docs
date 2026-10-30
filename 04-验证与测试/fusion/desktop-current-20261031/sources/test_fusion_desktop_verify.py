import builtins

import pypdfium2

from deploy.fusion_desktop_verify import pdf


def test_verification_pdf_does_not_require_a_pdf_authoring_dependency(monkeypatch):
    original_import = builtins.__import__

    def without_reportlab(name, *args, **kwargs):
        if name == "reportlab" or name.startswith("reportlab."):
            raise ModuleNotFoundError("ReportLab is absent from the application image")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_reportlab)
    data = pdf()
    expected = [
        "Synthetic desktop transport acceptance. No official bid.",
        "Second physical page. No model or score qualification.",
    ]
    document = pypdfium2.PdfDocument(data)
    try:
        assert len(document) == 2
        for index, text in enumerate(expected):
            page = document[index]
            textpage = page.get_textpage()
            try:
                assert textpage.get_text_range().strip() == text
            finally:
                textpage.close()
                page.close()
    finally:
        document.close()
