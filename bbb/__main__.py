import sys
from bertalign.bertalign import Bertalign

def main() -> int:
	print("Hello world, bilingual book builder is alive!")

	src_text = "Hello. How are you?"
	tgt_text = "Bonjour. Comment allez-vous ?"

	aligner = Bertalign(src=src_text, src_lang="en", tgt=tgt_text, tgt_lang="fr")
	aligner.align_sents()
	aligner.print_sents()
	return 0

if __name__ == "__main__":
	sys.exit(main())