## Wins

The biggest win was that `repo:alpen-dashboards` stopped being treated as a Grafana cleanup repo and became the coordination point for observability across `product:strata`, `product:strata-bridge`, and `product:mosaic`. That was not the February plan, but it was the right outcome. The panel redesign work mattered, but the higher leverage work was making alerts owned, actionable, and connected to runbooks.

person:krsnapaudel drove the thread from a loose dashboard refresh into something closer to operational infrastructure. By the end of the window, the most useful artifacts were not prettier panels; they were alert descriptions, ownership annotations, links into runbooks, and enough shared context that bridge and protocol engineers could reason about incidents without first decoding each other’s vocabulary.

We also got better cross-repo visibility. person:prajwolrg, person:MdTeach, and person:delbonis helped connect protocol-side symptoms in `repo:alpen` to dashboard views that previously looked too bridge-specific. On the bridge side, person:Rajil1213 and person:ProofOfKeags helped map `repo:strata-bridge` failure modes into operational language that could be alerted on rather than only debugged after the fact. That made the dashboards less decorative and more useful during review, staging, and incident triage.

Another concrete win was the devrel handoff surface. person:john-light’s involvement helped keep the runbook links and dashboard language understandable outside the small set of people who originally built the systems. That matters because the next incident will probably not happen when the original author of a panel is online.

## Challenges

The core challenge was scope expansion. We started with dashboard cleanup and ended up touching cross-repo observability ownership. That created useful pressure, but it also meant the work was always at risk of becoming “who owns reliability language for the whole stack?” instead of “fix these panels.”

Bridge and protocol teams also used different metric language for similar failure modes. A bridge queue stall, protocol liveness issue, delayed proof, finality lag, and indexing gap can all look operationally related, but the names and thresholds were inconsistent. This slowed reviews because we had to translate concepts before we could decide whether a panel or alert was correct.

Operational reliability work also kept competing with feature release review queues. This was especially visible when alert cleanup or dashboard changes needed attention from the same people reviewing protocol or bridge release work. The result was uneven momentum: short bursts of high-quality observability progress followed by stalls while feature review took priority.

We also deferred dashboard polish. That was the right tradeoff, but it left some surface-level roughness in `repo:alpen-dashboards`. The deeper issue is that we still do not have a single metrics SDK or naming convention that spans `repo:alpen`, `repo:strata-bridge`, and `repo:mosaic`. Without that, every dashboard improvement risks encoding local conventions instead of creating a shared operational model.

The follow-up backlog is larger than expected. Some of that is healthy discovery, but some is a sign that we did not bound the retro/output criteria tightly enough once the work expanded.

## What we'd do differently

I would define the ownership model earlier. The useful questions were not “which panel should change?” but “who gets paged, what do they inspect first, and what repo owns the fix?” We got there eventually, but we should have made that the starting frame.

I would also separate dashboard polish from alert correctness. Combining them made the work feel larger and less finished than it needed to. Alert ownership, runbook links, and metric naming should have been milestone one. Panel layout and visual cleanup should have been milestone two.

We should have created a bridge/protocol metric glossary earlier, even if incomplete. A short shared vocabulary would have reduced back-and-forth between person:Rajil1213, person:ProofOfKeags, person:prajwolrg, person:MdTeach, and person:delbonis when discussing similar failure modes across different repos.

Finally, we should have protected reliability review time from release review time. Observability work is easy to postpone because it rarely blocks a feature branch directly. But when it slips, the cost appears later during incidents, when the team pays in ambiguity.

## Action items

- person:krsnapaudel will maintain `repo:alpen-dashboards` as the coordination point for dashboard ownership, alert metadata, and runbook links.
- person:prajwolrg and person:delbonis will draft the first protocol-side metric glossary for `product:strata`.
- person:Rajil1213 and person:ProofOfKeags will draft the bridge-side equivalent for `product:strata-bridge`.
- person:MdTeach will review overlap between protocol and bridge failure-mode language and propose shared names where practical.
- person:john-light will review runbook wording for handoff clarity without turning it into external-facing documentation.
- Create a backlog item for a shared metrics SDK or naming package across `repo:alpen`, `repo:strata-bridge`, and `repo:mosaic`.
- Split future observability work into two tracks: alert correctness and dashboard polish.
- Reserve explicit review capacity for operational reliability work during release-heavy periods.
