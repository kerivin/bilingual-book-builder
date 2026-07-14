import logging
import threading
from lingua import Language
from sentence_splitter import SentenceSplitter
from wtpsplit import SaT


class SplitterWrapper:
    def split(self, text: str) -> list[list[str]]:
        return []


class SimpleWrapper(SplitterWrapper):
    def __init__(self, language: Language):
        self.splitter = SentenceSplitter(language.iso_code_639_1.name.lower())

    def split(self, text) -> list[list[str]]:
        paragraphs = []
        for para in text.split('\n\n'):
            para = para.strip()
            if not para:
                continue
            para_sentences = []
            for line in para.split('\n'):
                line = line.strip()
                if not line:
                    continue
                sentences = self.splitter.split(line)
                sentences = [s.strip() for s in sentences if s.strip()]
                para_sentences.extend(sentences)
            if para_sentences:
                paragraphs.append(para_sentences)
        return paragraphs


class SatWrapper(SplitterWrapper):
    def __init__(self, language: Language, model_name):
        self.splitter = SaT(
            model_name_or_model=model_name,
            language=language.iso_code_639_1.name.lower() if language else None,
            style_or_domain='ud' if language else None,
        )

    def split(self, text) -> list[list[str]]:
        paragraphs = []
        for para in text.split('\n\n'):
            para = para.strip()
            if not para:
                continue
            lines = [s for l in para.split('\n') if (s := l.strip())]
            para_sentences = self.splitter.split(lines, do_paragraph_segmentation=False)
            para_sentences = [s for sentences in para_sentences for sent in sentences if (s := sent.strip())]
            if para_sentences:
                paragraphs.append(para_sentences)
        return paragraphs


class Splitter:
    def __init__(self, model_name):
        self.model_name = model_name
        self.models = {}
        self.lock = threading.Lock()
        self.log = logging.getLogger(__name__)

    def run(self, text: str, language: Language) -> list[list[str]]:
        splitter = self._get_model(language)
        return splitter.split(text)

    def _get_model(self, language: Language) -> SplitterWrapper:
        if language in self.models:
            return self.models[language]

        with self.lock:
            if language in self.models:
                return self.models[language]

            if self.model_name:
                splitter = SatWrapper(language, self.model_name)
                self.log.info(f"Created SaT for {language.name}")
            else:
                splitter = SimpleWrapper(language)
                self.log.info(f"Created simple SentenceSplitter for {language.name}")

            self.models[language] = splitter
            return splitter