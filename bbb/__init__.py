import argparse

def main() -> int:
	parser = argparse.ArgumentParser(
		description='Create a bilingual EPUB from two EPUB books in different languages.'
	)
	parser.add_argument('-s', '--source', help='Path to source language EPUB')
	parser.add_argument('-t', '--target', help='Path to target language EPUB')
	parser.add_argument('-sl', '--source-language', default=None, help='Source language code, e.g. \"en\" (auto-detect if omitted)')
	parser.add_argument('-tl', '--target-language', default=None, help='Target language code, e.g. \"en\" (auto-detect if omitted)')
	parser.add_argument('-o', '--output', default='bbb.epub', help='Output EPUB file (default: bbb.epub)')
	parser.add_argument('--threads', default=1, help='How many parallel threads for book processing')

	args = parser.parse_args()
	if not args.source or not args.target:
		parser.print_help()
		return 0

	from bbb.bbb import BBB
	BBB(args.source, args.target, args.source_language, args.target_language, args.threads, False).run()

	return 0