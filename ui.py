# created 2026-11-25 by dani/Androtis
# last modified: 2026-11-25 by dani/Androtis
# Terminal UI Helper Code
from pathlib import Path

def question_line():
    print("-" * 52)
    print('\nEnter DIR, UPLOAD <local-file-path>, or q to quit: \n')
    print("> ")

def dir_response(dir: Path):
    print('-' * 52)
    print('\n')
    i = 0
    for item in dir.iterdir():
        i += 1
        print(f'File {i}: {item.name}\n')