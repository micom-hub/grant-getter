# grant-getter
This is a small pipeline to configure automated calls to the grants.gov api, save it to a sqlite to prevent duplication, write an XML file, publish it to a github page (grant-getter-feed), so that Slack RSS can pull from this location.

## Congifuring searches

Fields under api.defaults are applied to every search conducted by the pipeline, but specific searches can override these 

Supported search fields (within the serach2 API):

keyword: Searches within the opportunity text field
oppNum: Searches for opportunity numbers
eligibilities: restricts applicant eligibility with API codes
agencies: can restrict results to certain agency codes
oppStatuses: restrict by opportunity status
aln - assistance listing numbers
fundingCategories - restrict with funding category codes

