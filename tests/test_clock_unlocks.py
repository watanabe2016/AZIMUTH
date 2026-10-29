import sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import skin_manager as skins


class ClockUnlockTests(unittest.TestCase):
    def test_bundled_art_does_not_unlock_unimported_characters(self):
        with patch.object(skins,'list_themes',return_value=[]):
            self.assertEqual(skins.clock_skins(),[('default','默认人物')])
            self.assertEqual(skins.valid_clock_skin('anna'),'default')

    def test_imported_characters_remain_available_together(self):
        manifests=[{'id':'chiyo','builtin_theme':'chiyo','name':'千代'},
                   {'id':'anna','builtin_theme':'anna','name':'八奈见'}]
        with patch.object(skins,'list_themes',return_value=manifests):
            self.assertEqual(set(dict(skins.clock_skins())),{'default','chiyo','anna'})
            self.assertEqual(skins.valid_clock_skin('chiyo'),'chiyo')

    def test_custom_theme_without_clock_does_not_add_a_character(self):
        with patch.object(skins,'list_themes',return_value=[{'id':'test','name':'测试'}]), \
                patch.object(skins,'clock_path',return_value=''):
            self.assertEqual(len(skins.clock_skins()),1)
