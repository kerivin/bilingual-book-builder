import sys
import shutil

class ChapterMapper:
    def __init__(self, source_chapters, target_chapters):
        self.source = source_chapters
        self.target = target_chapters
        self.source_count = len(source_chapters)
        self.target_count = len(target_chapters)
        self.pairs = []
        self.unmatched_source = []
        self.unmatched_target = []
        self.terminal_width = shutil.get_terminal_size().columns

    def _chapter_str(self, chapters, idx):
        if idx < 0 or idx >= len(chapters):
            return "???"
        ch = chapters[idx]
        title = ch.get('title', f'Ch.{idx}')
        preview = ch.get('preview', '')
        if preview:
            return f"[{idx}] {title} — {preview}"
        else:
            return f"[{idx}] {title}"

    def show_chapter_lists(self):
        print("\nSource chapters:")
        for ch in self.source:
            print(f"  {self._chapter_str(self.source, ch['index'])}")
        print("\nTarget chapters:")
        for ch in self.target:
            print(f"  {self._chapter_str(self.target, ch['index'])}")

    def _chapter_title(self, chapters, idx):
        if idx < 0 or idx >= len(chapters):
            return ""
        return chapters[idx].get('title', f'Ch.{idx}')

    def _chapter_preview(self, chapters, idx):
        if idx < 0 or idx >= len(chapters):
            return ""
        return chapters[idx].get('preview', '')

    def _show_chapter_lists_compact(self):
        max_len = max(self.source_count, self.target_count)
        source_lines = []
        target_lines = []
        for i in range(max_len):
            if i < self.source_count:
                source_lines.append(f"[{self.source[i]['index']}] {self.source[i]['title']}")
            else:
                source_lines.append("")
            if i < self.target_count:
                target_lines.append(f"[{self.target[i]['index']}] {self.target[i]['title']}")
            else:
                target_lines.append("")
        col_width = max((len(s) for s in source_lines), default=30) + 4
        print("\n" + "Source chapters".ljust(col_width) + "Target chapters")
        print("-" * (col_width + max((len(t) for t in target_lines), default=30) + 4))
        for s_line, t_line in zip(source_lines, target_lines):
            print(f"{s_line:<{col_width}}{t_line}")

    def _print_confirmation_mapping(self):
        """Output a clean block-style mapping for final confirmation."""
        if not self.pairs and not self.unmatched_source and not self.unmatched_target:
            print("No mapping defined.")
            return

        print("\nProposed mapping:")
        for s_list, t_list in self.pairs:
            for si in s_list:
                source_title = self._chapter_title(self.source, si)
                source_preview = self._chapter_preview(self.source, si)
                for ti in t_list:
                    target_title = self._chapter_title(self.target, ti)
                    target_preview = self._chapter_preview(self.target, ti)
                    print(source_title)
                    print(target_title)
                    print(source_preview)
                    print(target_preview)
                    print("─" * self.terminal_width)
        for s in self.unmatched_source:
            print(self._chapter_title(self.source, s))
            print("(no target)")
            print(self._chapter_preview(self.source, s))
            print("")
            print("─" * self.terminal_width)
        for t in self.unmatched_target:
            print("(no source)")
            print(self._chapter_title(self.target, t))
            print("")
            print(self._chapter_preview(self.target, t))
            print("─" * self.terminal_width)

    def run(self):
        self.pairs = []
        self.unmatched_source = []
        self.unmatched_target = []

        si = 0
        ti = 0
        history = []

        self._show_chapter_lists_compact()
        print("\nMatch each pair. Press Enter to accept, 'S' to move to the next source chapter, 'T' to move to the next target chapter.\n")

        while si < self.source_count or ti < self.target_count:
            print("─" * self.terminal_width)
            if si < self.source_count:
                print(f"Source [S to next]: {self._chapter_str(self.source, si)}")
            else:
                print("Source: (no more)")
            if ti < self.target_count:
                print(f"Target [T to next]: {self._chapter_str(self.target, ti)}")
            else:
                print("Target: (no more)")
            print("─" * self.terminal_width)

            if si >= self.source_count and ti >= self.target_count:
                break
            if si >= self.source_count:
                action = 't'
            elif ti >= self.target_count:
                action = 's'
            else:
                try:
                    raw = input("[ENTER] Match / [S] Source Next Chapter / [T] Target Next Chapter / [B] Back / [Q] Quit: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print("\nAborted.")
                    return None
                action = raw.lower()

            if action == 'q':
                print("Aborted by user.")
                return None

            if action == 'b':
                if history:
                    last_si, last_ti, last_action, last_pair = history.pop()
                    if last_action == 'match':
                        self.pairs.remove(last_pair)
                    elif last_action == 'skip_source':
                        self.unmatched_source.pop()
                    elif last_action == 'skip_target':
                        self.unmatched_target.pop()
                    si, ti = last_si, last_ti
                    continue
                else:
                    print("Nothing to undo.")
                    continue

            if action == '':
                if si < self.source_count and ti < self.target_count:
                    pair = ([si], [ti])
                    self.pairs.append(pair)
                    history.append((si, ti, 'match', pair))
                    si += 1
                    ti += 1
                else:
                    print("Cannot match – one side empty. Use skip instead.")
                    continue
            elif action == 's':
                if si < self.source_count:
                    self.unmatched_source.append(si)
                    history.append((si, ti, 'skip_source', None))
                    si += 1
                else:
                    print("No more source chapters to skip.")
                    continue
            elif action == 't':
                if ti < self.target_count:
                    self.unmatched_target.append(ti)
                    history.append((si, ti, 'skip_target', None))
                    ti += 1
                else:
                    print("No more target chapters to skip.")
                    continue
            else:
                print("Invalid command. Try again.")
                continue

        self._print_confirmation_mapping()

        while True:
            try:
                confirm = input("Accept this mapping? [y]es / [n]o (redo) / [q]uit: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return None
            if confirm in ('y', 'yes', ''):
                return self._export_mapping()
            elif confirm in ('n', 'no'):
                print("Restarting matching...\n")
                return self.run()
            elif confirm in ('q', 'quit'):
                print("Aborted by user.")
                return None
            else:
                print("Please answer y, n, or q.")

    def _export_mapping(self):
        final = []
        for s_list, t_list in self.pairs:
            final.append((s_list, t_list))
        for s in sorted(self.unmatched_source):
            final.append(([s], []))
        for t in sorted(self.unmatched_target):
            final.append(([], [t]))
        return final