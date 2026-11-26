# created 2026-11-25 by dani/Androtis
# last modified: 2026-11-25 by dani/Androtis
# Terminal UI Helper Code
from pathlib import Path
import os
import time

# Number of lines certain bars will be used
num_lines = 100

# Universal OS Terminal Clear
def clear_terminal():
    os.system('cls' if os.name == 'nt' else 'clear')

# Line sweep UI Decorator
def line_sweep():
    line: str = ""
    for i in range(num_lines):
        if i % 2 == 0:
            line += '^'
        else:
            line += '-'
    print(line)

# Prompts the user intial question
def question_line():
    clear_terminal()
    line_sweep()
    print('\nEnter DIR, UPLOAD <local-file-path>, or q to quit: \n')
    print("> ")

# Reponse given by the DIR Command
def dir_response(dir: Path):
    clear_terminal()
    line_sweep()
    i = 0
    for item in dir.iterdir():
        i += 1
        print(f'File {i}: {item.name}')
    print('-' * num_lines)

# The Quit Message
def quit_message():
    clear_terminal()
    print("Thank you for using our service. Till Next Time.")
    time.sleep(2)
    clear_terminal()