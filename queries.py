FETCH_ALL_BY_ID = """
query ($num: Int!, $user: String = "neomutt", $repo: String = "neomutt") {
  repository(owner: $user, name: $repo) {
    issue(number: $num) {
      title
      url
      createdAt
      author {
        login
      }
    }
    pullRequest(number: $num) {
      title
      url
      createdAt
      author {
        login
      }
    }
    discussion(number: $num) {
      title
      url
      createdAt
      author {
        login
      }
      comments(first: 0) {
        totalCount
      }
      category {
        emojiHTML
      }
    }
  }
}
"""
