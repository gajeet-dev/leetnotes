USER_STATUS_QUERY = """
query globalData {
  userStatus {
    isSignedIn
    username
    isPremium
  }
}
"""

QUESTION_DETAILS_QUERY = """
query selectQuestion($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    content
    questionFrontendId
    title
    titleSlug
    difficulty
    categoryTitle
    topicTags {
      name
      slug
    }
  }
}
"""

SUBMISSION_LIST_QUERY = """
query submissionList($questionSlug: String!, $limit: Int, $offset: Int) {
  questionSubmissionList(
    questionSlug: $questionSlug
    limit: $limit
    offset: $offset
  ) {
    submissions {
      id
      statusDisplay
      lang
      timestamp
    }
  }
}
"""

RECENT_AC_SUBMISSIONS_QUERY = """
query recentAcSubmissions($username: String!, $limit: Int!) {
  recentAcSubmissionList(username: $username, limit: $limit) {
    title
    titleSlug
    timestamp
  }
}
"""

SUBMISSION_DETAILS_QUERY = """
query submissionDetails($submissionId: Int!) {
  submissionDetails(submissionId: $submissionId) {
    id
    code
    timestamp
    statusCode
    lang {
      name
      verboseName
    }
    runtimeDisplay
    memoryDisplay
  }
}
"""


QUESTION_DETAILS_QUERY_FULL = """
query questionDetail($titleSlug: String!) {
  languageList {
    id
    name
  }
  submittableLanguageList {
    id
    name
    verboseName
    isCompiledLang
  }
  statusList {
    id
    name
  }
  questionDiscussionTopic(questionSlug: $titleSlug) {
    id
    commentCount
    topLevelCommentCount
  }
  ugcArticleOfficialSolutionArticle(questionSlug: $titleSlug) {
    uuid
    chargeType
    canSee
    hasVideoArticle
  }
  question(titleSlug: $titleSlug) {
    title
    titleSlug
    questionId
    questionFrontendId
    questionTitle
    translatedTitle
    content
    translatedContent
    categoryTitle
    difficulty
    stats
    companyTagStatsV2
    topicTags {
      name
      slug
      translatedName
    }
    positionLevelTags {
      name
      nameTranslated
      slug
    }
    similarQuestionList {
      difficulty
      titleSlug
      title
      translatedTitle
      isPaidOnly
    }
    mysqlSchemas
    dataSchemas
    frontendPreviews
    likes
    dislikes
    isPaidOnly
    status
    canSeeQuestion
    enableTestMode
    metaData
    enableRunCode
    enableSubmit
    enableDebugger
    envInfo
    isLiked
    nextChallenges {
      difficulty
      title
      titleSlug
      questionFrontendId
    }
    libraryUrl
    adminUrl
    hints
    codeSnippets {
      code
      lang
      langSlug
    }
    exampleTestcaseList
    hasFrontendPreview
    featuredContests {
      titleSlug
      title
    }
    aiJudgingAvailable
  }
}
"""
