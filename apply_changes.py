"""
apply_changes.py — v2

Reads 'changes.txt' and applies FIND/REPLACE, INSERT_AFTER, INSERT_BEFORE,
DELETE operations to local files.

Whitespace-tolerant: matches ignoring leading/trailing spaces per line.
Comments aware: works even if the FIND block has slightly different
indentation than the target file.

Usage:
    python apply_changes.py
    python apply_changes.py --dry-run
    python apply_changes.py --file index.html
    python apply_changes.py --push
"""

import os
import re
import sys
import shutil
import argparse
import subprocess
from datetime import datetime

CHANGES_FILE = "changes.txt"
BACKUP_DIR = ".backups"


# ─────────────────────────────────────────────────────────────────────
# NORMALIZATION — ignore whitespace differences between lines
# ─────────────────────────────────────────────────────────────────────

def normalize(text):
    """
    Collapse all runs of whitespace (spaces, tabs, newlines) into
    a single space, and strip leading/trailing whitespace.
    Used for fuzzy matching.
    """
    return re.sub(r'\s+', ' ', text).strip()


def find_fuzzy(content, find):
    """
    Try to locate the FIND block in `content`, tolerating:
      - different indentation
      - different line endings
      - extra/missing blank lines

    Returns (start_index, end_index) or None.
    """
    # 1) Exact match first
    idx = content.find(find)
    if idx != -1:
        return (idx, idx + len(find))

    # 2) Normalized match: build a whitespace-insensitive pattern
    #    Split into non-empty lines, escape each, join with \s+.
    find_lines = [ln.strip() for ln in find.splitlines() if ln.strip()]
    if not find_lines:
        return None

    pattern_parts = [re.escape(ln) for ln in find_lines]
    pattern = r'\s+'.join(pattern_parts)

    m = re.search(pattern, content)
    if m:
        return (m.start(), m.end())

    return None


# ─────────────────────────────────────────────────────────────────────
# PARSER — reads the instruction blocks
# ─────────────────────────────────────────────────────────────────────

def parse_changes(text):
    """
    Parse the instruction file into a list of operations.
    Each operation: { file, op, find, replace, code, anchor }
    """
    ops = []
    blocks = re.split(r'===\s*BEGIN CHANGES\s*===', text)

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Remove trailing END CHANGE
        block = re.sub(r'===\s*END CHANGE\s*===.*', '', block, flags=re.DOTALL)
        block = block.strip()
        if not block:
            continue

        # Extract FILE:
        file_match = re.search(r'^FILE:\s*(.+?)\s*$', block, re.MULTILINE)
        if not file_match:
            continue
        filename = file_match.group(1).strip()

        def extract_section(keyword):
            pattern = re.compile(
                rf'^{re.escape(keyword)}:\s*\n(.*?)(?=^(?:FIND|REPLACE|INSERT_AFTER|INSERT_BEFORE|CODE|DELETE|FILE):|\Z)',
                re.MULTILINE | re.DOTALL
            )
            m = pattern.search(block)
            if not m:
                return None
            return m.group(1).rstrip('\n')

        find          = extract_section('FIND')
        replace       = extract_section('REPLACE')
        insert_after  = extract_section('INSERT_AFTER')
        insert_before = extract_section('INSERT_BEFORE')
        delete        = extract_section('DELETE')
        code          = extract_section('CODE')

        if find is not None and replace is not None:
            ops.append({'file': filename, 'op': 'replace',
                        'find': find, 'replace': replace})
        elif insert_after is not None and code is not None:
            ops.append({'file': filename, 'op': 'insert_after',
                        'anchor': insert_after, 'code': code})
        elif insert_before is not None and code is not None:
            ops.append({'file': filename, 'op': 'insert_before',
                        'anchor': insert_before, 'code': code})
        elif delete is not None:
            ops.append({'file': filename, 'op': 'delete', 'find': delete})

    return ops


# ─────────────────────────────────────────────────────────────────────
# FILE OPS
# ─────────────────────────────────────────────────────────────────────

def backup_file(path):
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(path)
    backup_path = os.path.join(BACKUP_DIR, f"{base}.{stamp}.bak")
    shutil.copy2(path, backup_path)
    return backup_path


def apply_replace(content, find, replace):
    result = find_fuzzy(content, find)
    if not result:
        return content, False
    start, end = result
    new_content = content[:start] + replace + content[end:]
    return new_content, True


def apply_insert_after(content, anchor, code):
    result = find_fuzzy(content, anchor)
    if not result:
        return content, False
    _, end = result
    new_content = content[:end] + "\n" + code + "\n" + content[end:]
    return new_content, True


def apply_insert_before(content, anchor, code):
    result = find_fuzzy(content, anchor)
    if not result:
        return content, False
    start, _ = result
    new_content = content[:start] + code + "\n" + content[start:]
    return new_content, True


def apply_delete(content, find):
    result = find_fuzzy(content, find)
    if not result:
        return content, False
    start, end = result
    new_content = content[:start] + content[end:]
    return new_content, True


def show_context(content, find, context_lines=3):
    """
    When a FIND fails, show a snippet of the file around where the
    first line of the FIND might be, to help debug.
    """
    first_line = find.splitlines()[0].strip() if find.splitlines() else ''
    if not first_line:
        return
    # Search for a loose match
    loose = re.escape(first_line[:40])
    m = re.search(loose, content)
    if not m:
        print(f"     (No partial match for {first_line[:40]!r} either)")
        return
    # Show ±context_lines around match
    lines = content[:m.start()].splitlines()
    start_line = max(0, len(lines) - 1)
    all_lines = content.splitlines()
    end_line = min(len(all_lines), start_line + context_lines * 2 + 1)
    print(f"     Closest match in file at line {start_line + 1}:")
    for i in range(start_line, end_line):
        marker = ">>" if i == start_line else "  "
        print(f"     {marker} {i+1:5d}: {all_lines[i]}")


def process_file(filename, ops, dry_run=False):
    if not os.path.exists(filename):
        print(f"❌ File not found: {filename}")
        return False

    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content
    applied = 0
    failed = 0

    for i, op in enumerate(ops):
        print(f"  [{i+1}/{len(ops)}] {op['op']}")
        if op['op'] == 'replace':
            content, ok = apply_replace(content, op['find'], op['replace'])
        elif op['op'] == 'insert_after':
            content, ok = apply_insert_after(content, op['anchor'], op['code'])
        elif op['op'] == 'insert_before':
            content, ok = apply_insert_before(content, op['anchor'], op['code'])
        elif op['op'] == 'delete':
            content, ok = apply_delete(content, op['find'])
        else:
            ok = False

        if ok:
            print(f"      ✅ Applied")
            applied += 1
        else:
            print(f"      ❌ NOT FOUND")
            if op['op'] == 'replace' or op['op'] == 'delete':
                show_context(original, op['find'])
            elif 'anchor' in op:
                show_context(original, op['anchor'])
            failed += 1

    if content != original and not dry_run:
        backup_file(filename)
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"  💾 Wrote {filename} ({applied} applied, {failed} failed)")
    elif content != original and dry_run:
        print(f"  💡 DRY RUN — would write {filename} ({applied} applied, {failed} failed)")
    else:
        print(f"  ⚠️  No changes to {filename}")

    return failed == 0


# ─────────────────────────────────────────────────────────────────────
# GIT PUSH
# ─────────────────────────────────────────────────────────────────────

def git_push(message="Auto: apply changes from changes.txt"):
    try:
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "push"], check=True)
        print("🚀 Pushed to GitHub")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  Git push failed: {e}")
    except FileNotFoundError:
        print("⚠️  Git not found — skipping push")


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--push', action='store_true')
    parser.add_argument('--file', type=str, default=None)
    args = parser.parse_args()

    if not os.path.exists(CHANGES_FILE):
        print(f"❌ {CHANGES_FILE} not found — create it and paste change blocks in it")
        sys.exit(1)

    with open(CHANGES_FILE, 'r', encoding='utf-8') as f:
        text = f.read()

    ops = parse_changes(text)
    if not ops:
        print("❌ No valid operations found in changes.txt")
        sys.exit(1)

    print(f"📖 Parsed {len(ops)} operation(s)\n")

    by_file = {}
    for op in ops:
        by_file.setdefault(op['file'], []).append(op)

    for filename, file_ops in by_file.items():
        if args.file and filename != args.file:
            continue
        print(f"📄 {filename} ({len(file_ops)} op(s))")
        process_file(filename, file_ops, dry_run=args.dry_run)
        print()

    if args.dry_run:
        print("💡 DRY RUN — no files modified")
    elif args.push:
        git_push()
    else:
        print("✅ Done. Review files, then commit/push manually or rerun with --push.")


if __name__ == '__main__':
    main()