# iOS 15 Session Width Contract Probe

This branch is diagnostic-only. It does not change the production source or
build an IPA.

## Question

When the legacy `UIHostingConfiguration` bridge asks a `UITextView` for its
intrinsic size before `bounds.width` exists, does returning the original
`UITextView` intrinsic width allow an unbounded width demand to enter the
SwiftUI/collection sizing chain?

## Comparison

- `currentFallback`: matches the production `SelectableMarkdownTextView`:
  valid bounds use `noIntrinsicMetric` plus `sizeThatFits`; invalid bounds
  return `super.intrinsicContentSize`.
- `noWidthDemandFallback`: identical except invalid bounds return
  `UIView.noIntrinsicMetric` for width and preserve only the original height.

Both paths run through production `LegacyHostingContent` and a byte-extracted
production `CodeBlockAttachment`. The fixture is a neutral five-code-block
structure and contains no private conversation text or recorded device
heights.

## Pass criteria

The matrix is diagnostically green only when:

1. `currentFallback` produces at least one width/container measurement beyond
   the expected bubble width, proving the probe can see the suspected failure.
2. `noWidthDemandFallback` stays bounded in all 24 samples across cold, long,
   shrink, and restore phases.

If the current path does not overflow, the result is explicitly
`inconclusive-current-fallback-did-not-reproduce-overflow`; it is not treated as
proof that the device issue is fixed. A green probe is evidence for the width
contract only, not full-app or iOS 15 device acceptance.
