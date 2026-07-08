import shutil
import numpy as np
from enum import IntEnum
import logging
from bbb import progress

class ChapterMapper:
    def __init__(self, source_chapters, target_chapters, keep_unmatched_source_chapters: bool, keep_unmatched_target_chapters: bool):
        self.source = source_chapters
        self.target = target_chapters
        self.keep_unmatched_source_chapters = keep_unmatched_source_chapters
        self.keep_unmatched_target_chapters = keep_unmatched_target_chapters

        self.source_count = len(source_chapters)
        self.target_count = len(target_chapters)
        self.chapter_pairs = []
        self.unmatched_source_chapters = []
        self.unmatched_target_chapters = []
        self.log = logging.getLogger(__name__)

    def _print_horizontal_line(self):
        terminal_width = shutil.get_terminal_size().columns
        self.log.info("─" * terminal_width)

    def _chapter_title(self, chapters, idx):
        if idx < 0 or idx >= len(chapters):
            return "???"
        return chapters[idx].get('toc_title', f'Ch.{idx}')

    def _chapter_preview(self, chapters, idx):
        if idx < 0 or idx >= len(chapters):
            return "???"
        return chapters[idx].get('preview', '???')

    def _chapter_str(self, chapters, idx):
        if idx < 0 or idx >= len(chapters):
            return "???"
        return f"{self._chapter_title(chapters, idx)} — {self._chapter_preview(chapters, idx)}"

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
        self.log.info("\n" + "Source chapters".ljust(col_width) + "Target chapters")
        self.log.info("-" * (col_width + max((len(t) for t in target_lines), default=30) + 4))
        for s_line, t_line in zip(source_lines, target_lines):
            self.log.info(f"{s_line:<{col_width}}{t_line}")

    def _show_chapter_mapping(self):
        if not self.chapter_pairs and not self.unmatched_source_chapters and not self.unmatched_target_chapters:
            self.log.info("No mapping defined.")
            return

        unmatched_src = set(self.unmatched_source_chapters) if self.keep_unmatched_source_chapters else set()
        unmatched_tgt = set(self.unmatched_target_chapters) if self.keep_unmatched_target_chapters else set()

        pairs_sorted = sorted(self.chapter_pairs, key=lambda p: p[0])

        prev_s = -1
        prev_t = -1

        self.log.info("\nMapping:")

        for s, t in pairs_sorted:
            for src_idx in sorted(unmatched_src):
                if prev_s < src_idx < s:
                    source_title = self._chapter_title(self.source, src_idx)
                    source_preview = self._chapter_preview(self.source, src_idx)
                    self.log.info(source_title + " - (no target)")
                    self.log.info(source_preview)
                    self.log.info("-")
                    self._print_horizontal_line()

            for tgt_idx in sorted(unmatched_tgt):
                if prev_t < tgt_idx and tgt_idx < t:
                    target_title = self._chapter_title(self.target, tgt_idx)
                    target_preview = self._chapter_preview(self.target, tgt_idx)
                    self.log.info("(no source) - " + target_title)
                    self.log.info("-")
                    self.log.info(target_preview)
                    self._print_horizontal_line()

            source_title = self._chapter_title(self.source, s)
            source_preview = self._chapter_preview(self.source, s)
            target_title = self._chapter_title(self.target, t)
            target_preview = self._chapter_preview(self.target, t)
            self.log.info(source_title + ' ─ ' + target_title)
            self.log.info(source_preview)
            self.log.info(target_preview)
            self._print_horizontal_line()

            prev_s, prev_t = s, t

        for src_idx in sorted(unmatched_src):
            if src_idx > prev_s:
                source_title = self._chapter_title(self.source, src_idx)
                source_preview = self._chapter_preview(self.source, src_idx)
                self.log.info(source_title + " ─ (no target)")
                self.log.info(source_preview)
                self.log.info("─")
                self._print_horizontal_line()

        for tgt_idx in sorted(unmatched_tgt):
            if tgt_idx > prev_t:
                target_title = self._chapter_title(self.target, tgt_idx)
                target_preview = self._chapter_preview(self.target, tgt_idx)
                self.log.info("(no source) ─ " + target_title)
                self.log.info("─")
                self.log.info(target_preview)
                self._print_horizontal_line()
    
    def _export_mapping(self):
        src_in_pairs = {s for s, t in self.chapter_pairs}
        tgt_in_pairs = {t for s, t in self.chapter_pairs}
        all_src = set(range(len(self.source)))
        all_tgt = set(range(len(self.target)))

        unmatched_src = sorted(all_src - src_in_pairs) if self.keep_unmatched_source_chapters else []
        unmatched_tgt = sorted(all_tgt - tgt_in_pairs) if self.keep_unmatched_target_chapters else []

        pairs_sorted = sorted(self.chapter_pairs, key=lambda p: p[0])

        ordered_chapters = []
        prev_s = -1
        prev_t = -1

        for s, t in pairs_sorted:
            for src_idx in unmatched_src:
                if prev_s < src_idx < s:
                    ordered_chapters.append((src_idx, None))
            for tgt_idx in unmatched_tgt:
                if prev_t < tgt_idx < t:
                    ordered_chapters.append((None, tgt_idx))
            ordered_chapters.append((s, t))
            prev_s, prev_t = s, t

        for src_idx in unmatched_src:
            if src_idx > prev_s:
                ordered_chapters.append((src_idx, None))
        for tgt_idx in unmatched_tgt:
            if tgt_idx > prev_t:
                ordered_chapters.append((None, tgt_idx))

        return ordered_chapters

    def _run_interactive(self) -> bool:
        self.chapter_pairs = []
        self.unmatched_source_chapters = []
        self.unmatched_target_chapters = []

        source_index = 0
        target_index = 0
        history = []

        prev_level = self.log.level
        self.log.setLevel(logging.INFO)
        self._show_chapter_lists_compact()
        self.log.setLevel(prev_level)
        
        print("\nMatch each pair.\n")

        while source_index < self.source_count or target_index < self.target_count:
            self._print_horizontal_line()
            if source_index < self.source_count:
                print(f"Source [S to skip]: {self._chapter_str(self.source, source_index)}")
            else:
                print("Source: (no more)")
            if target_index < self.target_count:
                print(f"Target [T to skip]: {self._chapter_str(self.target, target_index)}")
            else:
                print("Target: (no more)")
            self._print_horizontal_line()

            if source_index >= self.source_count and target_index >= self.target_count:
                break
            if source_index >= self.source_count:
                action = 't'
            elif target_index >= self.target_count:
                action = 's'
            else:
                try:
                    raw = input("[ENTER] Match / [S] Skip Source Chapter / [T] Skip Target Chapter / [B] Back / [Q] Quit: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    return False
                action = raw

            if action == 'q':
                return False

            if action == 'b':
                if history:
                    last_source_index, last_target_index, last_action, last_pair = history.pop()
                    if last_action == 'match':
                        self.chapter_pairs.remove(last_pair)
                    elif last_action == 'skip_source':
                        self.unmatched_source_chapters.pop()
                    elif last_action == 'skip_target':
                        self.unmatched_target_chapters.pop()
                    source_index, target_index = last_source_index, last_target_index
                    continue
                else:
                    print("Nothing to undo.")
                    continue

            if action == '':
                if source_index < self.source_count and target_index < self.target_count:
                    pair = (source_index, target_index)
                    self.chapter_pairs.append(pair)
                    history.append((source_index, target_index, 'match', pair))
                    source_index += 1
                    target_index += 1
                else:
                    print("Cannot match – one side empty. Use skip instead.")
                    continue
            elif action == 's':
                if source_index < self.source_count:
                    self.unmatched_source_chapters.append(source_index)
                    history.append((source_index, target_index, 'skip_source', None))
                    source_index += 1
                else:
                    print("No more source chapters to skip.")
                    continue
            elif action == 't':
                if target_index < self.target_count:
                    self.unmatched_target_chapters.append(target_index)
                    history.append((source_index, target_index, 'skip_target', None))
                    target_index += 1
                else:
                    print("No more target chapters to skip.")
                    continue
            else:
                print("Invalid command. Try again.")
                continue

        self._show_chapter_mapping()
        return True
    
    def run_interactive(self):
        while True:
            if self._run_interactive() == False:
                return None
            try:
                confirm = input("Accept this mapping? [Y]es / [N]o (redo) / [Q]uit: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return None
            if confirm in ('y', 'yes', ''):
                return self._export_mapping()
            elif confirm in ('n', 'no'):
                print("Restarting matching...\n")
            elif confirm in ('q', 'quit'):
                return None
            else:
                print("Please answer y, n, or q.")

    def run_auto(self, model, show: bool, threshold: float = 0.5):
        def chapter_signature(chapter):
            title = chapter.get('toc_title', '')
            full_text = chapter.get('full_text', '')
            if not full_text:
                return title

            text = full_text.replace('\n', ' ').strip()

            length = 1000
            if len(text) <= 2 * length:
                body = text
            else:
                body = text[:length] + ' [B_SEP] ' + text[-length:]

            return (title + ' [T_SEP] ' + body).strip()

        src_signature = [chapter_signature(ch) for ch in self.source]
        tgt_signature = [chapter_signature(ch) for ch in self.target]

        src_embs = model.encode(src_signature, convert_to_numpy = True, show_progress_bar = False)
        tgt_embs = model.encode(tgt_signature, convert_to_numpy = True, show_progress_bar = False)

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
                    score = dp[i-1, j-1] + sim[i-1, j-1]
                    if score > best:
                        best = score
                        best_pointer = MatchDirection.DIAGONAL
                if i > 0:
                    score = dp[i-1, j]
                    if score > best:
                        best = score
                        best_pointer = MatchDirection.UP
                if j > 0:
                    score = dp[i, j-1]
                    if score > best:
                        best = score
                        best_pointer = MatchDirection.LEFT
                dp[i, j] = best
                back[i, j] = best_pointer

        i, j = S, T
        chapter_pairs = []
        while i > 0 or j > 0:
            if back[i, j] == MatchDirection.DIAGONAL:
                i -= 1; j -= 1
                if sim[i, j] >= threshold:
                    chapter_pairs.append((i, j))
                else:
                    self.unmatched_source_chapters.append(i)
                    self.unmatched_target_chapters.append(j)
            elif back[i, j] == MatchDirection.UP:
                i -= 1
                self.unmatched_source_chapters.append(i)
            elif back[i, j] == MatchDirection.LEFT:
                j -= 1
                self.unmatched_target_chapters.append(j)

        chapter_pairs.reverse()
        self.unmatched_source_chapters.reverse()
        self.unmatched_target_chapters.reverse()

        self.chapter_pairs = chapter_pairs
        if show:
            prev_level = self.log.level
            self.log.setLevel(logging.INFO)
            self._show_chapter_mapping()
            self.log.setLevel(prev_level)

        self.log.info("\nAuto-matching completed.")
        return self._export_mapping()
