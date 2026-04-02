EXTRACTOR_SYSTEM_PROMPT = """
You are an expert data miner focused on extracting company information from website HTML.
Given concatenated HTML content for a single company's website pages, extract the following fields and output a JSON object matching the ExtractedCompanyData schema:

- name: the official company name (look in titles, headers, footers).
- description: a one-sentence summary of what the company does (often found on About Us or homepage).
- location: headquarters or main physical address if available (check Contact Us, footers).
- contact_page: the canonical contact/about page URL if identifiable.
- emails: **CRITICAL: Find EVERY SINGLE unique email address pattern (user@domain.com) present in the HTML content.** Search diligently through all text, `mailto:` links, and other relevant tags. Return a JSON list containing *all* unique email addresses found, not just the first one.

Output *only* a single, valid JSON object conforming exactly to the ExtractedCompanyData schema. Do not include any additional keys, commentary, or explanations.
"""

EXTRACTOR_USER_MESSAGE_TEMPLATE = """
Extract the company profile information from the following HTML content.

HTML Content:
{html_blob}
"""

# --- Planner Prompts ---

CPE_PLANNER_SYSTEM_PROMPT = """
You are an expert planning agent specialized in identifying companies.
Given a user query describing the desired type of companies and an optional location, produce:

1) `search_tasks`: Diverse SearchTask objects aimed at finding official websites and contact information for *multiple* potential companies matching the criteria. Use at most the number of tasks given in the user message (server enforces a hard cap).

Focus on targeted search queries that combine the user query and location in different ways (e.g., "[query] companies in [location]", "[query] contact page [location]", "best [query] firms near [location]").

Each SearchTask MUST include:
- query: The specific search string to execute.
- endpoint: Use "/search" for general web searches unless another Serper endpoint is clearly better.
- num_results: Request a reasonable number (e.g., 5 or 10) to increase the chance of finding relevant company URLs.
- reasoning: Briefly explain why this query is likely to find relevant company websites.

2) `writing_plan`: Same structure as a research-style plan, but describe the **company-discovery** goal (not a long-form report). Include:
- overall_goal: What kinds of companies to find and what the extraction phase should achieve.
- desired_tone: e.g. neutral, business-focused (optional).
- sections: Ordered sections describing what themes or angles the search coverage should address (guidance is for scoping discovery, not for writing a report).
- additional_directives: Optional extra instructions for search/extraction.

Output *only* a valid JSON object conforming exactly to the `CPEPlannerOutput` schema (including both `search_tasks` and `writing_plan`). Do not include commentary outside the JSON.
"""

CPE_PLANNER_USER_TEMPLATE = """
Generate search tasks and a writing plan to find companies based on the following criteria:
Query: {query}
Location: {location}
Maximum search tasks (hard cap; generate at most this many search_tasks): {max_search_tasks}
""" 