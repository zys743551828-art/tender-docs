"""Fixed isolated CPU fixture generation; no configuration/credentials inherited."""

if __name__ == "__main__":
    import sys

    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 60), sys.stdin.buffer.read().decode(), fontname="china-s", fontsize=12)
    sys.stdout.buffer.write(document.tobytes())
