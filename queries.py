FETCH_ALL_BY_ID = """
query ($num: Int!, $user: String = "neomutt", $repo: String = "neomutt") {
  repository(owner: $user, name: $repo) {
    issue(number: $num) {
      number
      title
      url
      createdAt
      author {
        login
      }
    }
    pullRequest(number: $num) {
      number
      title
      url
      createdAt
      author {
        login
      }
    }
    discussion(number: $num) {
      number
      title
      url
      createdAt
      author {
        login
      }
      comments {
        totalCount
      }
      category {
        emojiHTML
      }
    }
  }
}
"""
