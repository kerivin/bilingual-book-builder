import logging
import threading
from lingua import Language
from sentence_splitter import SentenceSplitter
from wtpsplit import SaT


def _paragraphs_of_lines(text):
    for para in text.split('\n\n'):
        lines = [line.strip() for line in para.split('\n') if line.strip()]
        if lines:
            yield lines


class SimpleWrapper:
    def __init__(self, language: Language):
        self.splitter = SentenceSplitter(language.iso_code_639_1.name.lower() if language else 'en')

    def split(self, text) -> list[list[str]]:
        paragraphs = []
        for lines in _paragraphs_of_lines(text):
            para_sentences = []
            for line in lines:
                para_sentences.extend(s.strip() for s in self.splitter.split(line) if s.strip())
            if para_sentences:
                paragraphs.append(para_sentences)
        return paragraphs


class SatWrapper:
    def __init__(self, language: Language, model_name):
        try:
            self.splitter = SaT(
                model_name_or_model=model_name,
                language=language.iso_code_639_1.name.lower() if language else None,
                style_or_domain='ud' if language else None,
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "SaT: couldn't find language-specific adaptation, using common model instead")
            self.splitter = SaT(model_name_or_model=model_name)

    def split(self, text) -> list[list[str]]:
        paragraphs = []
        for lines in _paragraphs_of_lines(text):
            split_lines = self.splitter.split(lines, do_paragraph_segmentation=False)
            para_sentences = [s for sentences in split_lines for sent in sentences if (s := sent.strip())]
            if para_sentences:
                paragraphs.append(para_sentences)
        return paragraphs


class Splitter:
    def __init__(self, model_name):
        self.model_name = model_name
        self._splitters = {}
        self.lock = threading.Lock()
        self.log = logging.getLogger(__name__)

    def run(self, text: str, language: Language) -> list[list[str]]:
        return self._get_model(language).split(text)

    def _get_model(self, language: Language):
        if language in self._splitters:
            return self._splitters[language]

        with self.lock:
            if language in self._splitters:
                return self._splitters[language]

            if self.model_name:
                splitter = SatWrapper(language, self.model_name)
                self.log.info(f"Created SaT for {language.name if language else None}")
            else:
                splitter = SimpleWrapper(language)
                self.log.info(f"Created simple SentenceSplitter for {language.name if language else None}")

            self._splitters[language] = splitter
            return splitter