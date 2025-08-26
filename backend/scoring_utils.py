# scoring_utils.py
import imagehash

class ConfidenceScorer:
    @staticmethod
    def score_text(ref_text: str, tgt_text: str) -> float:
        """
        Jaccard‐style score of word overlap.
        """
        ref_set = set(ref_text.lower().split())
        if not ref_set:
            return 1.0
        tgt_set = set(tgt_text.lower().split())
        return len(ref_set & tgt_set) / len(ref_set)

    @staticmethod
    def score_image(ref_hash: str, tgt_hash: str) -> float:
        """
        1 − (Hamming distance between p‐hashes / hash length).
        """
        h1 = imagehash.hex_to_hash(ref_hash)
        h2 = imagehash.hex_to_hash(tgt_hash)
        max_bits = h1.hash.size
        dist = h1 - h2
        return 1 - (dist / max_bits)
