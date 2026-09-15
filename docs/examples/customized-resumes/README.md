# Customized Resume Examples

These three example résumés demonstrate how the same base résumé (`_config/profile/base-resume/base-resume.md`) gets customized for different roles, drawing different experience bullets from the full career history (`_config/profile/full-experience/full-experience.md`).

## Why These Three?

Each example targets a **different seniority level and emphasis**, covering the most common customization angles for a senior engineering leader:

| Example | Target Role | What It Demonstrates |
|---------|------------|---------------------|
| Principal Application Security Engineer | Principal IC | AppSec/SCA, autonomous remediation, IAM, and attack-path analysis |
| Staff Backend Platform Engineer | Staff IC | Python and Go services, workflow processing, data scale, reliability, and strict typing |
| Staff Cloud Infrastructure Engineer | Staff IC | AWS, Terraform, auto-scaling control planes, CI/CD, and deployment automation |

## How to Use

1. **As few-shot examples for the customizer:** The pipeline reads these files (filenames listed in `config.json` → `few_shot_examples`) and includes them in the customization prompt. The customizer learns the expected structure, tone, and level of detail from these examples.

2. **As a reference for your own examples:** Replace these with your own customized resumes. Keep 3-4 examples that cover different role types you typically apply for. The goal is to show the customizer what a good customized resume looks like — not to provide every possible variation.

## Replacing With Your Own

1. Create 3-4 customized resumes for roles you've actually applied to (or realistic examples)
2. Name them descriptively: `YourName - Company Role.md`
3. List the filenames in `config.json` → `few_shot_examples`
4. Remove the Squall examples from this directory

## Truthfulness

Every bullet in these examples is traceable to `profile/full-experience/full-experience.md`. The truthfulness reviewer (`agent-profiles/truthfulness-reviewer.md`) verifies this — no bullet appears on a customized resume that isn't in the full experience. Your examples should follow the same pattern.
