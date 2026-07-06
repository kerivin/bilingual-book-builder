import argparse

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Create a bilingual EPUB from two EPUB books in different languages.'
    )
    parser.add_argument('-s', '--source', type=str, required=True, help='Path to source (original) language EPUB')
    parser.add_argument('-t', '--target', type=str, required=True, help='Path to target (translation) language EPUB. Its settings have priority over source EPUB')
    parser.add_argument('-sl', '--source-language', type=str, default=None, help='Source language code, e.g. \"en\" (auto-detect if omitted)')
    parser.add_argument('-tl', '--target-language', type=str, default=None, help='Target language code, e.g. \"ru\" (auto-detect if omitted)')
    parser.add_argument('-o', '--output', type=str, default='bilingual.epub', help='Output EPUB file (default: bilingual.epub)')
    parser.add_argument('--threads', type=int, default=1, help='How many parallel threads for book processing')
    parser.add_argument('--auto-match-chapters', nargs='?', const=0.6, default=None, type=float, help='Auto match chapters. Optionally, you can provide a similarity threshold value (0.0-1.0)')
    parser.add_argument('--only-match-chapters', action='store_true', help='Only auto matching, no EPUB generated (requires --auto-match-chapters)')
    parser.add_argument('--keep-source-chapters', action='store_true', default=False, help='Whether to keep source chapters that have no target translation')
    parser.add_argument('--keep-target-chapters', action='store_true', default=False, help='Whether to keep target chapters that have no source original')
    parser.add_argument('--model', type=str, default='LaBSE', help='Name or path to sentence embedding model (download LaBSE if omitted)')

    args = parser.parse_args()
    if args.only_match_chapters and args.auto_match_chapters is None:
        parser.print_help()
        return 0

    from bbb import BBB
    BBB(
        args.source,
        args.target,
        args.source_language,
        args.target_language,
        args.output,
        args.threads,
        args.auto_match_chapters,
        args.only_match_chapters,
        args.keep_source_chapters,
        args.keep_target_chapters,
        args.model
    ).run()

    return 0