# -*- coding: utf-8 -*-
"""Pop up a real file picker, validate whatever you choose, and show the result.
Run with: uv run python pick_and_validate.py
"""

import tkinter as tk
from tkinter import filedialog

from parsers.upload_validator import validate_upload


def main():
    root = tk.Tk()
    root.withdraw()  # we only want the file dialog, not a full blank window

    file_path = filedialog.askopenfilename(
        title="Choose a file to upload",
        filetypes=[
            ("Supported documents", "*.docx *.xlsx *.pdf"),
            ("All files", "*.*"),
        ],
    )

    if not file_path:
        print("No file selected.")
        return

    print(f"\nYou selected: {file_path}")
    result = validate_upload(file_path)

    if result["valid"]:
        print(f"ACCEPTED — {result['segment_count']} segments extracted.")
        print("\nFirst 3 segments:")
        for seg in result["segments"][:3]:
            print(f"  [{seg['type']}] {seg['text'][:100]}")
    else:
        print(f"REJECTED — {result['error']}")


if __name__ == "__main__":
    main()
