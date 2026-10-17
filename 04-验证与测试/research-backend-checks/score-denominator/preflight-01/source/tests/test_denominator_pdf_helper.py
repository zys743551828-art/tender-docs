"""Isolated two-page synthetic conflict document, without inherited credentials."""

if __name__ == "__main__":
    import sys

    import fitz

    document = fitz.open()
    for text in sys.stdin.buffer.read().decode().split("\f"):
        page = document.new_page()
        page.insert_text((40, 60), text, fontname="china-s", fontsize=12)
    sys.stdout.buffer.write(document.tobytes())
