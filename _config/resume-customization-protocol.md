# Resume Customization Guide

<!-- ICM Layer 3 — read by both the automated `devin -p` customizer calls and the debug
     `customizer` subagent profile. Defines how resumes are customized for specific JDs. -->

## What to Emphasize

I am a hands-on Distinguished Software Engineer targeting Staff, Principal, Distinguished, and equivalent senior individual-contributor roles at strong product, platform, security, and AI companies. Tailor applications toward backend, platform, AppSec/SCA, cloud infrastructure, distributed systems, developer tooling, workflow automation, and agentic AI work.

Search constraints are authoritative: pure IC roles; remote roles that explicitly allow work from Virginia or hybrid roles in Northern Virginia/Washington, DC; no Maryland hybrid roles and no relocation; no clearance-required roles; minimum $300,000 annual total compensation, while retaining roles whose total compensation is unknown. Optimize primarily for mission and domain fit.

When a JD emphasizes a specific theme, draw from the relevant part of my background:
- **AppSec/SCA:** Lead with Heeler's multi-ecosystem remediation engine, CycloneDX graph traversal, minimum-safe-upgrade calculation, and vulnerability remediation
- **AI/agentic systems:** Lead with Heeler's LangGraph and AWS Bedrock Fix Now feature, repository-scoped agent memory, package-manager proxy, execution tracking, and concurrency controls
- **Backend/platform systems:** Lead with asynchronous workflow workers, Redis-backed checkpointing, PostgreSQL and SQLAlchemy, multi-tenant performance work, and Python or Go services
- **Reliability/scale:** Lead with scheduler deadlock and double-enqueue fixes, large-tenant query optimization, DivvyCloud's 10M+ IAM rows per day, and Capital One products receiving 100,000+ calls per day
- **Cloud/DevOps:** Lead with the DivvyCloud auto-scaling control plane, AWS and Terraform experience, ECS Fargate and RDS, and Capital One's blue-green deployment automation
- **Technical leadership:** Lead with ownership of major products and migrations, strict typing across the Python backend, onboarding an eight-engineer team, and the onboarding system used by 30+ engineers

## What to Downplay or Omit

Keep the résumé centered on senior individual-contributor impact. Specifically:
- Downplay mobile, desktop, and early-career web work unless the JD explicitly values it
- Condense roles before 2019 when space is needed; retain enough detail to show progression and relevant technical breadth
- Do not imply formal people-management responsibility. Team onboarding and technical leadership are supported; engineering-management scope is not
- Do not overstate AI/ML experience beyond the agentic remediation work explicitly documented at Heeler
- Omit technologies that are not relevant to the JD rather than expanding the technology section indiscriminately

## Formatting Preferences

- **Section order:** Preserve exactly — Summary → Technology → Experience → Education. Don't add, remove, or reorder top-level sections.
- **Summary:** Keep it concise and tailor its emphasis to the JD without adding unsupported claims.
- **Bullet style:** No periods at the end of bullets. Match the base résumé exactly.
- **Technology:** Keep the flat technology list. Prioritize relevant documented technologies without inventing proficiency.
- **Font/spacing:** Not relevant — resumes are markdown only, graded and reviewed via text. No PDF generation step in the current pipeline.

### Length & Bullet Optimization Rules

* **Target:** 70 rendered lines. **Hard ceiling:** 75 rendered lines. The few-shot examples range from 69–75 rendered lines and serve as the practical reference.
* **Character Wrap Threshold:** Bullet lines wrap at **102 characters** per line (Calibri 11pt, 0.5" margins).
  * ≤ 102 characters = 1 line
  * 103–204 characters = 2 lines
  * 205–306 characters = 3 lines

### Self-Measurement & Trimming Protocol

1. **Calculate Rendered Line Count:**
   $$\text{Total Lines} = \sum \text{ceil}\left(\frac{\text{Bullet Character Length}}{102}\right)$$

2. **Self-measure with `count_lines.py`:** After writing the resume, run `python3 -m pipeline.helpers.count_lines <path> --json` to measure rendered lines. Do not attempt to count lines manually — LLMs cannot reliably count rendered lines.

3. **Self-trim if over the ceiling:** If total exceeds 75 rendered lines, trim the least JD-relevant bullets until ≤ 75. Target ~70. Up to 3 trim passes. If still over 75 after 3 passes, accept and proceed — the grader is the quality gate, not the line count.

## Tone

Match the tone in the generic resume exactly - not too many buzzwords, minimal corporate jargon, to the point but not too informal.

## Source Material — The Reference Pool

The configured documents form the reference pool the customizer draws from to match the JD. Select and rephrase the strongest truthful material from this pool. Every claim must appear in the base résumé or full-experience source.

- **Base resume** (`_config/profile/base-resume/base-resume.md`) — the customization BASE. Every tailored résumé starts from a fresh copy of this template. Preserve its structure, formatting, section order, and existing bullets as the starting point.
- **Full experience** (`_config/profile/full-experience/full-experience.md`) — the SOURCING reference. Consult it for material not on the base résumé, including additional bullets, skills, technologies, and earlier-role detail.
- **Few-shot examples** (`docs/examples/customized-resumes/`) — the three configured Val-specific examples demonstrate AppSec, backend/platform, and cloud-infrastructure emphasis. Use them for transformation patterns, never as independent factual authority.
- **Never build a customized résumé directly from full experience.** The output is always the base résumé plus selected sourced material, formatted to match the base. Full experience is a source, not a template.

## Customization Strategy

When customizing the resume for a specific JD, the agent may:
- **Preserve chronological job order.** Never reorder employers or roles — keep them in the exact order they appear in the base resume. Within a role, preserve the existing bullet order by default. Reorder bullets only when it creates a material improvement in JD alignment, and never move a bullet above the bullets that describe the role's fundamental scope, ownership, or core responsibilities.
- **Rewrite bullet wording** to mirror the JD's language (e.g. if the JD says "platform reliability," adjust a bullet that says "availability" to use "reliability" — but only if the meaning is identical)
- **De-emphasize or shorten bullets** that are irrelevant to the JD to stay within the 75 rendered line ceiling (see Length & Bullet Optimization Rules above)
- **Reorder or trim the Technology list** to prioritize documented capabilities emphasized by the JD
- **Condense roles before 2019** if space is needed for more relevant recent content
- **Pull new bullets, skills, technologies, or earlier-role experience from full experience** onto the base résumé when the JD demands something not on the base, but only when that material is stated explicitly in the source. Format pulled material to match the base résumé's style.

The agent may NOT:
- Add new bullets, skills, technologies, or experience that appear nowhere in the base résumé and nowhere in full experience. When in doubt, omit.
- Fabricate or infer experience from adjacent work — every added claim must be directly stated in the base résumé or full experience, not inferred
- Add an objective or cover-letter section, or remove a required top-level section
- Change company names, job titles, or employment dates

## Handling Gaps

When the JD asks for something not on the base resume:
- **In full experience:** Pull the relevant material onto the base résumé and format it to match.
- **Adjacent experience exists:** Highlight the documented adjacent experience without relabeling it as the requested skill.
- **No relevant experience (in either doc):** Omit. Don't fabricate. Let the grade reflect the gap honestly.
- **Partial experience:** Emphasize the relevant portion of what I have without overstating it. Don't reword to imply deeper expertise than I have.

## Grading Criteria

### JD Grade (Step 2) — how well does this job fit my background?
- **9-10:** My experience directly maps to the role's core requirements at an appropriate senior IC level and scale. The company and technical scope are a clear fit.
- **7-8:** Strong overlap on most requirements. A few gaps but nothing critical. Worth customizing a resume.
- **5-6:** Some overlap but significant gaps or the role is too junior/senior. Marginal.
- **Below 5:** Poor fit. Don't bother.
- **Threshold to proceed:** > 8

### Resume Grade (Step 4) — how well does the customized resume match the JD?
- **9-10:** Every core JD requirement is addressed by specific, quantified experience. No gaps on critical items. The resume reads like it was written for this specific role.
- **7-8:** Most core requirements are addressed. Minor gaps. The resume is well-tailored but not perfect.
- **5-6:** Partial tailoring. Some requirements unaddressed. The resume feels generic for this JD.
- **Below 5:** Poor match. The resume doesn't address the JD's core needs.
- **Threshold to proceed:** ≥ 9

**What the grade measures:** How likely it is that the resume will be chosen for an interview: demonstrated impact at similar scale, leadership scope alignment, keyword/language alignment with the JD, and absence of gaps on critical requirements. Not just keyword matching — the experience behind the keywords must be real and relevant.

## Hard Rules

1. **Analyze documents COMPLETELY rather than in isolated snippets.** Ensure that your changes are not duplicative of other bullets, and make sense holistically for both the section and the overall document.
2. **When modifying resumes, preserve the exact existing section header structure** (e.g., "Experience", "Education", "Skills"). Update content strictly within those existing headers without adding or modifying top-level sections.
3. **Ensure the formatting matches throughout.** For instance, don't add periods where there weren't any before, make sure the tabs and spacing match throughout, etc.
4. **Never invent or inflate metrics.** If a bullet says "20% improvement," don't change it to "30%" to better match the JD. The numbers are real.
5. **Never invent job titles, company names, or employment dates.** These are immutable facts.
6. **Never add skills, technologies, or experiences that appear nowhere in the base résumé AND nowhere in full experience.** Every added claim must be directly stated in one of those sources, not inferred from adjacent work. When in doubt, omit. If the JD asks for something absent from both, see "Handling Gaps" above.
7. **Don't change terms arbitrarily unless they have the same meaning.** For instance, don't replace "monolith" with "distributed" — those are not interchangeable. Only swap terms when the meaning is identical.

## Few-Shot Examples

Three Val-specific few-shot résumés are configured:

1. **Principal Application Security Engineer** — AppSec/SCA, autonomous remediation, IAM, and attack-path analysis
2. **Staff Backend Platform Engineer** — Python and Go services, workflow processing, data scale, reliability, and strict typing
3. **Staff Cloud Infrastructure Engineer** — AWS, Terraform, auto-scaling control planes, CI/CD, and deployment automation

Use them to learn surgical rewording, source selection, compression, and format fidelity. Never copy JD text into the résumé, treat the target headline as an employment title, introduce unsupported consulting language, or duplicate bullets within a role.
