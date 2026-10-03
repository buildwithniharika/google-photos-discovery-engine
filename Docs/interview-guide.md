# Interview guide: finding a photo you just saved

**Opportunity:** Edited, saved, or new photos missing from the main view (`oa-69e09da1`)  
**Frozen run:** `2026-10-02T102736_d4e5bd`  
**Use with:** [`discovery-readout.md`](discovery-readout.md)  
**Session length:** 45 minutes. One recent incident, told in order. No prototype and no feature brainstorm.

These questions turn the area's seven research questions into a single interview. Each one names the evidence gap it is there to close.

## Screener

Recruit 8–12 people. At least six whose incident is an edit, a crop, a download, or a save. Up to three whose incident is “I can see it on the website or in Recently added, and the main app does not show it.” The corpus for this area is 52 of 69 Android, so fill Android first and keep two or three iPhone users if they pass the screener.

Stop when the only problem is storage price, a full backup quota, or a photo the person knowingly deleted. Those are a different study (milestone loss and vanished year-blocks in the readout).

Read this as a short form. Record the answers.

1. Do you use the Google Photos app on your phone at least once a week?
   - Yes → continue
   - No → stop
2. In the last 90 days, did you edit, crop, download, upload, or save a photo or video, and then have trouble finding it in Google Photos or when sharing it to another app?
   - Yes → continue
   - No → stop
3. When that happened, did you still believe the photo existed? For example, you had just saved it, or you could see it in another folder, another app, or on the Google Photos website.
   - Yes → continue
   - No, it was gone and I was trying to get a backup back → stop
4. Which phone do you use for Google Photos? (Android / iPhone / both). Either can continue. Note it.
5. Can you describe one specific time this happened in the last 90 days, including what you were trying to do with the photo?
   - A concrete incident → recruit
   - Only a general complaint → stop

Record, and do not treat as quotas: whether the incident was an edit, crop, download, upload, or save; whether they eventually found the photo somewhere else; Android or iPhone.

## How to run it

- Start from their incident. Ask questions 1–6 about that incident before you ask about other times.
- Let them finish the sequence of places they looked before you offer examples (Recently added, the website, another app).
- If they start designing a feature, bring them back to what they expected to see and what they concluded.
- Write down their words for the place they expected the photo to be. That phrase is the result we need.
- After the session, tag the incident as one of: just-saved copy, web-versus-app, screenshot or album filing, or something else. The cluster currently mixes these.

## Questions

### 1. Set the task

“Walk me through the last time this happened, starting from the moment you decided to look for the photo. What were you trying to do with it?”

**Closes:** we do not know how often the task is time-sensitive, or what it costs when the photo is missing (`d8abe474`, `87811740`, `03616256`).  
**Probe:** posting, attaching, selling, sending to a person, printing. How soon did you need it?

### 2. Name the action

“What had you done to the photo before you looked? Edit, crop, download, upload, save a copy, save from a shared album, or something else? How long before you looked?”

**Closes:** the feedback lists actions and does not say which one causes the trouble (`35b72493`, `7a18899e`, `263320f6`).  
**Probe:** if they did more than one thing, which one they blame.

### 3. What they actually remembered

“When you started looking, what did you remember about the photo itself? The people, the place, roughly when it was taken, words on it, or only that you had just saved it?”

**Closes:** cues in the corpus are the situation and the source app. Forgotten details are almost never stated (`268a4406`, `d8abe474`, `df1af45c`).  
**Listen for:** a rich description of the photo, versus memory of the action only. Count these across interviews. A rich description that then failed keyword search belongs in the keyword-search area, not this one.

### 4. The first place

“Where did you look first, and why there?”

**Closes:** the expected location of an edited copy, and the first step, are unknown (`e51d96e9`, `c6a9ca53`, `268a4406`).  
**Probe:** Camera folder, main library, Recent, the app they wanted to post from.

### 5. The rest of the path, in order

“What did you try after that, in order? Include other apps, the website, folders, and search if you used them. Where did you stop?”

**Closes:** attempts are listed in the corpus (Recently added, device folders, the website, the delete flow) without a sequence.  
**Probe:** about how long, and the moment they decided to stop. Do not read the list unless they stall.

### 6. What a side view meant

“Did you find the photo somewhere else while the place you needed it stayed empty? What did you think had happened to it?”

**Closes:** we do not know whether people trust the photo is safe or believe it is lost (`a65cf50d`, `1a5c9439`, `b6cc4836`).  
**Probe:** “still in Photos, just not here” versus “gone.” What would have told you it was safe?

### 7. Shared libraries

“If this photo came from a shared album or someone else’s library, where did you expect your saved copy to show up?”

**Closes:** the mental model for shared-library saves is unknown, and the corpus only has a few of these (`4ca61254`, `ac3aec2a`).  
**Skip** if question 2 was not a shared album. Note the skip.

### 8. The website, an older app, or a notification

“Tell me about a time the Google Photos website, an older version of the app, or a Photos notification showed you photos that the current app would not. What made you check there? What did you remember about those photos?”

**Closes:** what triggers a cross-surface check, and the cues people hold for an older item (`cbf8db58`, `172225d5`, `df1af45c`).  
**Listen for:** a specific memory (kids, a couple of years ago, a notification) versus “the library looks shorter.” The second pattern is the vanished-blocks area; note it and do not recruit the rest of the session around it.

### 9. How often, and which action

“In a typical month, how many times do you edit, download, or save a photo and then have trouble finding it? Which of those actions causes the most trouble?”

**Closes:** there is no frequency by action (`35b72493`, `263320f6`).  
**Probe:** every time, some photos, or rarely. Ask for the last month, not for “usually.”

### 10. What counted as found

“When you stopped, had you found it? If yes, where was it, and what did you see that made you sure it was the right photo? If no, what would you have needed to see to be sure?”

**Closes:** the corpus does not say what “found” means when a side view has the file and the main view does not.  
**Stay on the incident.** This question is about their definition of success, not about a feature they want built.

### 11. What they did afterward

“What did you do once you stopped looking inside Google Photos? Use another app, ask someone, contact support, post without it, or leave it?”

**Closes:** outcomes after the breakdown, including whether they give up, are barely described (`d8abe474`, `87811740`).  
**Probe:** whether they changed how they save photos after that.

## After each interview

Fill this in before the next session.

| Field | Notes |
| --- | --- |
| Incident type | just-saved copy / web-versus-app / screenshot or album / other |
| Cue they actually had | action only / content of the photo / both |
| First place they looked | |
| Where it actually was, if anywhere | |
| Did a side view mean “safe” or “lost”? | |
| Still this opportunity? | yes / move to old-capture-dates / move to backup loss / move to keyword search |

## What would change the decision

- If most incidents are “the website has my library and the app does not,” treat that as its own follow-up and keep the interview guide’s questions 4–6 and 8. The edit/save claim would be too narrow for the whole area.
- If most incidents are “I remembered what the photo showed and search returned the wrong set,” stop this guide and switch to the keyword-search area (`oa-53a15320`).
- If people are sure the file is gone and no other surface has it, that is the milestone and vanished-block finding. Close those out of this study.
- If the edit/save path is consistent and people share an expected landing place, the next research step after these interviews is a concept test of that expectation. Do not start the concept test from the public posts alone.
