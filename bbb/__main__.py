import sys
from bertalign.bertalign import Bertalign

def main() -> int:
	print("Hello world, bilingual book builder is alive!")

	src_sents = ["Hello.", "How are you?"]
	tgt_sents = ["Bonjour.", "Comment allez-vous ?"]

	aligner = Bertalign(src_sents, tgt_sents)
	aligner.align_sents()
	aligner.print_sents()
	return 0

if __name__ == "__main__":
	sys.exit(main())