**Highlights**

Glock is now public. We tagged the release during the November 4-12 ship window and published the demo, benchmark bundle, and whitepaper appendix together rather than splitting them across separate drops. That was the right call: the release is easier to evaluate because the implementation, measurement harness, and technical claims all point at the same reviewed artifact set.

The main shape of the launch changed late, but for good reasons. Earlier drafts carried a stronger performance headline than we could responsibly defend before public review caught up with launch timing. person:john-light trimmed the launch post to match the reviewed claims, and person:AaronFeickert kept the technical framing tight enough that we did not overstate what Glock proves today. The public version is narrower, but it is much more durable.

The blocker that cost us the most time was benchmark reproducibility. The core numbers were not the issue; making the benchmark bundle independently usable was. person:Hakkush-07, person:Zk2u, and person:mukeshdroid pushed through the cleanup so external readers can rerun and inspect the results instead of treating the post as a trust-us announcement.

We also deliberately deferred protocol integration notes. Glock is relevant to Alpen protocol work, but this release needed to stay focused on the primitive, the implementation, and the measured behavior. Integration design deserves its own review loop rather than being wedged into a launch artifact.

**What Shipped**

We shipped the public Glock tag in `repo:g16` after final CI went green. The release includes the demo path we wanted people to be able to run immediately, rather than only pointing them at paper results.

We published the benchmark bundle from `repo:hash-benchmarks` with the release. The bundle is still not the final word on all possible environments or parameter choices, but it is now structured enough that third parties can reproduce the tested setup and understand the measurement assumptions.

We published the Glock appendix through `repo:Technical-Whitepaper`. This gives reviewers a stable technical reference for the construction and the claims we are willing to stand behind right now.

We coordinated the public-facing release in `repo:alpen`, with launch copy narrowed to the reviewed surface area. The final language avoids implying protocol integration status, production readiness, or broad performance dominance that we have not yet substantiated under open review.

We also resolved the release-order problem. Instead of publishing the post first and backfilling technical material later, we shipped the post, demo, benchmarks, and appendix as a single bundle. That reduced ambiguity and gave external readers a concrete path from claim to code to measurement.

**Coming Up**

The next workstream is external review. person:AaronFeickert will keep driving the technical response loop, with person:mukeshdroid and person:Hakkush-07 close on benchmark and construction questions. We should expect reviewers to probe both the security framing and the measurement setup.

The benchmark harness needs a second pass after launch. The current bundle is good enough to publish, but we should make it easier to run across more machines and easier to compare against future changes. person:Zk2u and person:ceyhunsen are the natural owners for turning the release harness into something closer to a maintained measurement workflow.

Protocol integration notes are still pending. person:storopoli should be pulled in when we reopen that thread, especially around how Glock fits into concrete protocol paths rather than abstract “could use this” language. We should treat that as design work, not launch cleanup.

Devrel follow-through is also important. person:john-light will track the public questions that come in and separate genuine technical review from requests for simplified explanation. Any answer that changes claims should route back through person:AaronFeickert before we make it public.

**Q&A Summary**

The first question was whether we lost too much by cutting the stronger performance headline. Short answer: no. We lost some launch sharpness, but we gained accuracy. The stronger version depended on benchmark interpretation that was not yet reviewed enough for a public claim.

There was also a question about whether benchmark reproducibility should have blocked the tag. Yes. A cryptographic performance claim without a runnable benchmark bundle creates avoidable trust debt. The final CI wait was frustrating, but cutting the tag before reproducibility was stable would have made the launch weaker.

Several people asked why protocol integration notes were not included. The answer is scope control. Glock’s public release establishes the primitive and its measured behavior. Integration into Alpen needs separate threat-model, implementation, and product-context review. Shipping both at once would have made the release harder to review and easier to misread.

We also discussed whether the appendix is too dense for a public launch. It probably is for casual readers, but that is acceptable. The launch post is the entry point; the appendix is for people who want to verify the technical substance.

**Shoutouts**

person:AaronFeickert for driving the release through the uncomfortable middle: narrowing claims, holding the technical line, and keeping the artifact set coherent.

person:mukeshdroid for staying on the benchmark and review details when the remaining work was mostly precision, not novelty.

person:Hakkush-07 for pushing benchmark reproducibility over the line and helping make the public bundle something reviewers can actually use.

person:Zk2u for infra support on the benchmark path and release readiness, especially around the late blocker.

person:ceyhunsen for helping close the research/engineering gap around what needed to be stable before publication.

person:john-light for cutting launch copy back to what we could defend, even though the earlier headline was easier to sell.

person:storopoli for being ready to pick up the protocol integration thread after release instead of forcing it into this launch.
