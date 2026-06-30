import shutil
import numpy as np
from enum import IntEnum
from sentence_transformers import SentenceTransformer

class ChapterMapper:
    def __init__(self, source_chapters, target_chapters, keep_unmatched_source_chapters, keep_unmatched_target_chapters):
        self.source = source_chapters
        self.target = target_chapters
        self.keep_unmatched_source_chapters = keep_unmatched_source_chapters
        self.keep_unmatched_target_chapters = keep_unmatched_target_chapters
        self.source_count = len(source_chapters)
        self.target_count = len(target_chapters)
        self.pairs = []
        self.unmatched_source = []
        self.unmatched_target = []

    def _print_horizontal_line(self):
        terminal_width = shutil.get_terminal_size().columns
        print("─" * terminal_width)

    def _chapter_title(self, chapters, idx):
        if idx < 0 or idx >= len(chapters):
            return "???"
        return chapters[idx].get('title', f'Ch.{idx}')

    def _chapter_preview(self, chapters, idx):
        if idx < 0 or idx >= len(chapters):
            return "???"
        return chapters[idx].get('preview', '???')

    def _chapter_str(self, chapters, idx):
        if idx < 0 or idx >= len(chapters):
            return "???"
        return f"{self._chapter_title(chapters, idx)} — {self._chapter_preview(chapters, idx)}"

    def show_chapter_lists(self):
        print("\nSource chapters:")
        for ch in self.source:
            print(f"  {self._chapter_str(self.source, ch['index'])}")
        print("\nTarget chapters:")
        for ch in self.target:
            print(f"  {self._chapter_str(self.target, ch['index'])}")

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

    def _show_chapter_mapping(self):
        if not self.pairs and not self.unmatched_source and not self.unmatched_target:
            print("No mapping defined.")
            return

        target_to_source = {}
        for s_list, t_list in self.pairs:
            for ti in t_list:
                target_to_source[ti] = s_list[0] if s_list else None

        print("\nProposed mapping:")

        max_len = max(self.source_count, self.target_count)
        for i in range(max_len):
            if self.keep_unmatched_source_chapters and i < self.source_count and i in self.unmatched_source:
                source_title = self._chapter_title(self.source, i)
                source_preview = self._chapter_preview(self.source, i)
                print(source_title + " - (no target)")
                print(source_preview)
                print("-")
                self._print_horizontal_line()

            if self.keep_unmatched_target_chapters and i < self.target_count and i in self.unmatched_target:
                target_title = self._chapter_title(self.target, i)
                target_preview = self._chapter_preview(self.target, i)
                print("(no source) - " + target_title)
                print("-")
                print(target_preview)
                self._print_horizontal_line()

            if i < self.target_count and i in target_to_source:
                si = target_to_source[i]
                source_title = self._chapter_title(self.source, si)
                source_preview = self._chapter_preview(self.source, si)
                target_title = self._chapter_title(self.target, i)
                target_preview = self._chapter_preview(self.target, i)
                print(source_title + ' - ' + target_title)
                print(source_preview)
                print(target_preview)
                self._print_horizontal_line()

    def _run_interactive(self) -> bool:
        self.pairs = []
        self.unmatched_source = []
        self.unmatched_target = []

        si = 0
        ti = 0
        history = []

        self._show_chapter_lists_compact()
        print("\nMatch each pair. Press Enter to accept, 'S' to move to the next source chapter, 'T' to move to the next target chapter.\n")

        while si < self.source_count or ti < self.target_count:
            self._print_horizontal_line()
            if si < self.source_count:
                print(f"Source [S to next]: {self._chapter_str(self.source, si)}")
            else:
                print("Source: (no more)")
            if ti < self.target_count:
                print(f"Target [T to next]: {self._chapter_str(self.target, ti)}")
            else:
                print("Target: (no more)")
            self._print_horizontal_line()

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
                    return False
                action = raw

            if action == 'q':
                print("Aborted by user.")
                return False

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

        self._show_chapter_mapping()
        return True

    def _export_mapping(self):
        final = []
        for s_list, t_list in self.pairs:
            final.append((s_list, t_list))
        if self.keep_unmatched_source_chapters:
            for s in sorted(self.unmatched_source):
                final.append(([s], []))
        if self.keep_unmatched_target_chapters:
            for t in sorted(self.unmatched_target):
                final.append(([], [t]))
        return final
    
    def run_interactive(self):
        while True:
            if self._run_interactive() == False:
                return None
            try:
                confirm = input("Accept this mapping? [y]es / [n]o (redo) / [q]uit: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return None
            if confirm in ('y', 'yes', ''):
                return self._export_mapping()
            elif confirm in ('n', 'no'):
                print("Restarting matching...\n")
            elif confirm in ('q', 'quit'):
                print("Aborted by user.")
                return None
            else:
                print("Please answer y, n, or q.")

    def run_auto(self, threshold: float = 0.5, gap_penalty: float = 0.3):
        """Automatically map source↔target by aligning first sentences."""
        from bertalign.bertalign import model_name
        model = SentenceTransformer(model_name)

        def chapter_signature(chapter, sentence_count: int = 3):
            full_text = chapter.get('full_text', '')
            if not full_text:
                return ""

            parts = full_text.split('\n\n', 1)
            body = parts[1] if len(parts) > 1 else full_text
            sentences = body.replace('\n', ' ').split('. ')

            first_part = ' '.join(sentences[:sentence_count])[:400]

            if len(sentences) >= sentence_count:
                last_part = ' '.join(sentences[-sentence_count:])[:400]
            elif sentences:
                last_part = sentences[-1][:200]
            else:
                last_part = ""

            return (first_part + " " + last_part).strip()

        src_signature = [chapter_signature(ch) for ch in self.source]
        tgt_signature = [chapter_signature(ch) for ch in self.target]

        src_embs = model.encode(src_signature, convert_to_numpy = True)
        tgt_embs = model.encode(tgt_signature, convert_to_numpy = True)

        # Normalise
        src_embs = src_embs / np.linalg.norm(src_embs, axis = 1, keepdims = True)
        tgt_embs = tgt_embs / np.linalg.norm(tgt_embs, axis = 1, keepdims = True)

        sim = np.dot(src_embs, tgt_embs.T)  # shape (S, T)

        S, T = len(src_signature), len(tgt_signature)
        # DP table: dp[i][j] = best score up to i-1, j-1
        dp = np.full((S+1, T+1), -1e9)
        dp[0, 0] = 0.0

        class MatchDirection(IntEnum):
            DIAGONAL = 0
            UP = 1 # skip source
            LEFT = 2 # skip target
        back = np.zeros((S+1, T+1), dtype=int)

        for i in range(S+1):
            for j in range(T+1):
                if i == 0 and j == 0:
                    continue
                best = -1e9
                best_pointer = None
                if i > 0 and j > 0:
                    score = dp[i-1, j-1] + (sim[i-1, j-1] if sim[i-1, j-1] >= threshold else -gap_penalty)
                    if score > best:
                        best = score
                        best_pointer = MatchDirection.DIAGONAL
                if i > 0:
                    score = dp[i-1, j] - gap_penalty
                    if score > best:
                        best = score
                        best_pointer = MatchDirection.UP
                if j > 0:
                    score = dp[i, j-1] - gap_penalty
                    if score > best:
                        best = score
                        best_pointer = MatchDirection.LEFT
                dp[i, j] = best
                back[i, j] = best_pointer

        # Traceback
        i, j = S, T
        pairs = []
        while i > 0 or j > 0:
            if back[i, j] == MatchDirection.DIAGONAL:
                i -= 1; j -= 1
                if sim[i, j] >= threshold:
                    pairs.append(([i], [j]))
                else:
                    # treated as gap – both unmatched
                    self.unmatched_source.append(i)
                    self.unmatched_target.append(j)
            elif back[i, j] == MatchDirection.UP:
                i -= 1
                self.unmatched_source.append(i)
            elif back[i, j] == MatchDirection.LEFT:
                j -= 1
                self.unmatched_target.append(j)

        # Reverse because we collected from end to start
        pairs.reverse()
        self.unmatched_source.reverse()
        self.unmatched_target.reverse()

        self.pairs = pairs
        self._show_chapter_mapping()
        return pairs
