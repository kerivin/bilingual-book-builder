import argparse
import logging
from tqdm import tqdm

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Create a bilingual EPUB from two EPUB books in different languages.'
    )
    parser.add_argument('-s', '--source', type=str, required=True, help='Path to source (original) language EPUB')
    parser.add_argument('-t', '--target', type=str, required=True, help='Path to target (translation) language EPUB. Its settings have priority over source EPUB')
    parser.add_argument('-sl', '--source-language', type=str, default=None, help='Source language code, e.g. \"en\" (auto-detect if omitted)')
    parser.add_argument('-tl', '--target-language', type=str, default=None, help='Target language code, e.g. \"ru\" (auto-detect if omitted)')
    parser.add_argument('-o', '--output', type=str, default='bilingual', help='Output EPUB file (default: bilingual)')
    parser.add_argument('--threads', type=int, default=1, help='How many parallel threads for book processing')
    parser.add_argument('--auto-match-chapters', nargs='?', const=0.6, default=None, type=float, help='Auto match chapters. Optionally, you can provide a similarity threshold value (0.0-1.0)')
    parser.add_argument('--only', choices=['auto-match', 'extract'], default=None, help='Only extract chapters or auto-match chapters without generating a new EPUB')
    parser.add_argument('--keep-source-chapters', action='store_true', default=False, help='Whether to keep source chapters that have no target translation')
    parser.add_argument('--keep-target-chapters', action='store_true', default=False, help='Whether to keep target chapters that have no source original')
    parser.add_argument('--model', type=str, default='LaBSE', help='Name or path to sentence embedding model (download LaBSE if omitted)')
    parser.add_argument('-v', '--verbosity', choices=['silent', 'progress', 'verbose'], default='progress', help='Silent (no progress), Progress (show progress bars), Verbose (all messages)')
    
    args = parser.parse_args()
    if args.only == 'auto-match' and args.auto_match_chapters is None:
        parser.print_help()
        return 0

    def get_log_level(verbosity):
        match verbosity:
            case 'silent':
                return logging.ERROR
            case 'progress':
                return logging.WARNING
            case _:
                return logging.INFO

    logger = logging.getLogger('bbb')
    logger.setLevel(get_log_level(args.verbosity))
    class TqdmStream:
        def write(self, msg):
            if msg and msg.strip():
                tqdm.write(msg, end='')
        def flush(self):
            pass

    handler = logging.StreamHandler(stream=TqdmStream())
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)

    logging.getLogger('bertalign').setLevel(get_log_level(args.verbosity))
    logging.getLogger('bertalign').addHandler(handler)

    bars = {}
    def progress_callback(phase_id, description: str, step: int, total: int, message: str = None):
        if args.verbosity == 'silent':
            return

        if step == 0:
            bars[phase_id] = tqdm(total = total, desc = description or message or phase_id)
        elif phase_id in bars:
            current = bars[phase_id].n
            if step > current:
                bars[phase_id].update(step - current)
            if message:
                bars[phase_id].set_postfix_str(message)
            if step >= total:
                bars[phase_id].close()
                del bars[phase_id]

    from bbb import BBB
    BBB(
        args.source,
        args.target,
        args.source_language,
        args.target_language,
        args.output,
        args.threads,
        args.auto_match_chapters,
        args.only,
        args.keep_source_chapters,
        args.keep_target_chapters,
        args.model,
        args.verbosity,
        progress_callback
    ).run()

    return 0