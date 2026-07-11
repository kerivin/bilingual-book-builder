import logging
import threading
from lingua import Language
from sentence_splitter import SentenceSplitter
from wtpsplit import SaT

class SplitterWrapper:
    def __init__(self):
        pass

    def split(self, text):
        return None

class SimpleWrapper(SplitterWrapper):
    def __init__(self, language: Language):
        super().__init__()
        self.splitter = SentenceSplitter(
            language.iso_code_639_1.name.lower()
        )
    
    def split(self, text):
        return self.splitter.split(text)

class SatWrapper(SplitterWrapper):
    def __init__(self, language: Language, model_name):
        super().__init__()
        self.splitter = SaT(
            model_name_or_model=model_name,
            language=language.iso_code_639_1.name.lower() if language else None,
            style_or_domain='ud' if language else None,
        )
    
    def split(self, text):
        return self.splitter.split(text)

class ChapterSplitter:
    def __init__(self, model_name):
        self.simple_split = model_name is None
        self.model_name = model_name
        self.models = {}
        self.lock = threading.Lock()
        self.log = logging.getLogger(__name__)
    
    def run(self, text, language: Language):
        splitter = self._get_model(language)
        return splitter.split(text)
        
    def _get_model(self, language: Language) -> SplitterWrapper:
        if language in self.models:
            return self.models[language]

        with self.lock:
            if language in self.models:
                return self.models[language]

            if self.simple_split:
                splitter = SimpleWrapper(language)
                self.log.info(f"Created simple SentenceSplitter for {language.name}")
            else:
                splitter = SatWrapper(language, self.model_name)
                self.log.info(f"Created SaT for {language.name}")

            self.models[language] = splitter
            return splitter