from wtpsplit import SaT
from lingua import Language

class ChapterSplitter:
    def __init__(self, model_name):
        self.model_name = model_name
        self.models = {}
    
    def run(self, text, language: Language):
        model = self._get_model(language)
        return model.split(
            text,
            do_paragraph_segmentation = True,
            split_on_input_newlines = True
        )
        
    def _get_model(self, language: Language) -> SaT:
        if not language in self.models:
            self.models[language] = SaT(
                model_name_or_model=self.model_name,
                language=language.iso_code_639_1.name.lower(),
                style_or_domain='ud',
            )
        return self.models[language]