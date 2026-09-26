# Shared spike code

Status: throwaway (Phase 0). Not part of the product.

This folder holds code that more than one spike uses, so that spikes never import each other. It is empty for now.

The first planned content is an "effects" module that several experiments need:
- a classifier that labels a function's signature change as breaking, needing review, or compatible;
- a check of whether each call still matches the new signature;
- a search for references found by name only, labelled as low confidence;
- test-file patterns;
- a fixed list of what the analysis did not cover.
