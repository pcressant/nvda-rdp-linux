# Temporary BRLTTY override.
# The nixpkgs package used by this prototype does not currently enable/build
# the Polkit integration required for BrlAPI access in this setup.
# Remove this overlay once the upstream/nixpkgs package provides it.
final: prev: {
  brltty = prev.brltty.overrideAttrs (old: {
    buildInputs = (old.buildInputs or [ ]) ++ [
      final.polkit
    ];
  });
}
