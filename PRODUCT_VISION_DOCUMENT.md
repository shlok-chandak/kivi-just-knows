# PRODUCT VISION

## Where does Kivi stand today?

Kivi is a voice native dictation and writing tool. Today it primarily acts as a dictation tool the user speaks, Kivi converts it into written language, styles and phonetic memory improve the output.

Hey Kivi is another layer where you can speak to Kivi directly and query it. As of right now, it is stateless and is unaware of its prior interactions with you.

## What is Kivi's long term vision?

To become the tool that makes interacting with computers effortless. Being the user's primary interface to a computer.

- Kivi understands the user's working styles and preferences and helps them accomplish tasks across multiple applications without the user providing context regarding their work multiple times
- Over the course of time, Kivi develops an understanding of the user, remembering what is useful and forgetting things that don't matter.

## Who is Kivi for?

Kivi is for professionals who use numerous applications daily and collaborate with a lot of people. These people have their work spread across documents, emails, Slack which makes it difficult to transfer context across different applications.

Example: Founders, engineers, product managers

Kivi is also for people whose primary job is communicating with customers, candidates or external stakeholders. They spend a significant portion of their day writing and communicating.

Example: HR, sales, marketing and customer support.

## How can Kivi help them?

I have bucketed Hey Kivi commands into multiple use cases. These are:

| Type | Examples | Is memory used? |
|---|---|---|
| Recall Conversations | “Hey Kivi, what did I tell Aaditya about the sales project yesterday?”<br>“Hey Kivi, what pricing did we decide about ABC Inc?”<br>“Hey Kivi, with whom did I discuss tiered pricing?” | Yes, memory is used here to recall specific episodes and information from the user's work. |
| Reuse Decisions | “Hey Kivi, can you create a document with pricing page changes?”<br>“Hey Kivi, can you make a prompt to solve the bug I discussed with Pranav?”<br>“Hey Kivi, draft a text to Aaditya giving a summary of the sales project.” | Yes, memory is used to recall past events and then create useful artefacts like documents, prompts and messages from them. |
| Styles & Formatting | “Hey Kivi, rewrite this LLM output to sound more like me.”<br>“Hey Kivi, rephrase this in my casual work style.”<br>“Hey Kivi, make this shorter and more formal.” | Yes, Kivi needs to learn about the user preferences for specific styling and formatting but for general styles it doesn't need much context. |
| Trust | “Hey Kivi, delete everything you know about the sales project”<br>“Hey Kivi, don't remember anything from Whatsapp” | This bucket gives the user access to control what Kivi remembers about them. |
| Computer Use | “Hey Kivi, open Slack”<br>“Hey Kivi, open the sales doc I was working on yesterday”<br>“Hey Kivi, show me my calendar” | These are the use cases which do not need dedicated memory but rather proper tooling. |

## What does semantic memory allow Kivi to become?

Semantic memory allows Kivi to become intelligent and aware of the user's work.

- It enables personalised outputs without repeating the same prompt.
- Execute tasks with less context from the user.
- Improving the user experience by learning user preference and styles.
- Always be able to show why it said something, and what it chose not to keep.

## What should Kivi remember (and not remember)?

Based on the above Hey Kivi commands,

**Kivi should remember/store the following:**

- Facts: Names (Aaditya) , relationships (Aaditya is a coworker), project (sales project) & company names (ABC Inc.)
- Preferences: Sentence structure, punctuation, capitalization, formalness, formatting etc.
- Episodes: Things that happened in a particular conversation (sales project episode)
- Raw events: For auditing and explainability (proof that Kivi thinks you prefer something)

**Kivi should never assume the following**

- Never assume a particular fact is permanent and cannot change.
- Never assume something that isn't in its memory.
- Never assume personal facts related to sensitive topics like religion, politics, race, health etc.
- Never conclude user preference from a small set of patterns.

Kivi should never store information pertaining sensitive topics.
