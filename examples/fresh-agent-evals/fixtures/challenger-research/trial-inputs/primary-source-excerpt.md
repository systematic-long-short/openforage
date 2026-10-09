# Archived primary-source excerpt (attributed)

Provenance: retrieved from the public arXiv abstract page on 2026-09-23.
This excerpt is supplied inside the eval workspace because the fixture
forbids network access; reading it counts as reading the archived source,
never as inventing a reopening.

- Source title: "Illuminating search spaces by mapping elites"
- Authors: Jean-Baptiste Mouret, Jeff Clune
- Venue/identifier: arXiv:1504.04909 [cs.AI], submitted 20 Apr 2015
- URL: https://arxiv.org/abs/1504.04909
- DOI: https://doi.org/10.48550/arXiv.1504.04909
- License pointer: see the arXiv page's "view license" link for the full text

Excerpt (abstract, verbatim):

> Many fields use search algorithms, which automatically explore a search
> space to find high-performing solutions: chemists search through the space
> of molecules to discover new drugs; engineers search for stronger,
> cheaper, safer designs, scientists search for models that best explain
> data, etc. The goal of search algorithms has traditionally been to return
> the single highest-performing solution in a search space. Here we describe
> a new, fundamentally different type of algorithm that is more useful
> because it provides a holistic view of how high-performing solutions are
> distributed throughout a search space. It creates a map of high-performing
> solutions at each point in a space defined by dimensions of variation that
> a user gets to choose. This Multi-dimensional Archive of Phenotypic Elites
> (MAP-Elites) algorithm illuminates search spaces, allowing researchers to
> understand how interesting attributes of solutions combine to affect
> performance, either positively or, equally of interest, negatively. ...
> MAP-Elites produces a large diversity of high-performing, yet
> qualitatively different solutions, which can be more helpful than a single,
> high-performing solution.

Mechanism note for the task (not a superiority claim): MAP-Elites keeps an
archive keyed by user-chosen dimensions of variation and searches for
high-performing solutions per cell instead of returning only one global best.
Any comparison against `random_weighted` still requires a campaign with
matched budgets, equal completed evaluations, and preregistered analysis.
