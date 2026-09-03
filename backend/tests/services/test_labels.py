from app.services.labels import canonical_label


def test_case_and_plural_variants_share_one_key():
    # The four spellings that between them held 122 tag instances in the archive.
    for variant in ("Smartphones", "Smartphone", "smartphones", "SMARTPHONE"):
        assert canonical_label(variant) == "smartphone"


def test_punctuation_is_ignored():
    assert canonical_label("Fashion & Accessories") == canonical_label("Fashion Accessories")
    assert canonical_label("Self-hosting") == canonical_label("Self-Hosting")
    assert canonical_label("K-pop") == canonical_label("K-Pop")


def test_ies_plurals_fold_to_y():
    assert canonical_label("Celebrities") == canonical_label("Celebrity")
    assert canonical_label("Conspiracy Theories") == canonical_label("Conspiracy Theory")


def test_es_plurals_fold():
    assert canonical_label("Watches") == canonical_label("Watch")
    assert canonical_label("Wildfires") == canonical_label("Wildfire")


def test_words_whose_s_is_part_of_the_stem_survive():
    # Stripping these would collide unrelated labels or mangle the word.
    for word in ("Analysis", "Chaos", "Business", "Census"):
        assert canonical_label(word) == word.lower()


def test_already_singular_ies_words_are_left_alone():
    # "Series" must not become "sery"; both spellings still have to agree.
    assert canonical_label("TV Series") == canonical_label("TV series")
    assert "series" in canonical_label("TV Series")


def test_distinct_labels_are_not_merged():
    assert canonical_label("Model Kits") != canonical_label("Plastic Models")
    assert canonical_label("Figures") != canonical_label("Action Figures")
    assert canonical_label("Film Review") != canonical_label("Film Festival")


def test_unusable_input_gives_empty_string():
    for value in (None, "", "   ", 42, "!!!", ["x"]):
        assert canonical_label(value) == ""
