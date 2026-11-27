"""Add the comment-queue scanner. Run from the distAuto folder after `git pull`:
    python3 _scanner/apply.py
"""
import os, shutil
here = os.path.dirname(os.path.abspath(__file__))
os.chdir(os.path.dirname(here))
shutil.copy(os.path.join(here, "scan.py"), "scan.py")
shutil.copy(os.path.join(here, "accounts.csv"), "accounts.csv")
shutil.copy(os.path.join(here, "comment-scan.yml"), ".github/workflows/comment-scan.yml")
print("Added scan.py, accounts.csv and the 'Comment queue scan' workflow.")
