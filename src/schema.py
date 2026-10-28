"""
Defines the Pydantic models of the German pop culture dataset.

The models that Gemini fills (``ItemAnnotation`` and its parts) are separate
from the records written to disk (subclasses of ``RecordBase``).
Vocabularies are ``Literal`` aliases, because google-genai drops the field
descriptions of ``Enum`` classes when it builds the response schema. All
fields of the models sent to Gemini are required; absence is expressed with
"Not applicable", an empty list, false or null.
"""
import types
import typing
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"

# ISO 3166-2 codes of the German states
StateCode = Literal["DE-BW", "DE-BY", "DE-BE", "DE-BB", "DE-HB", "DE-HH", "DE-HE", "DE-MV", "DE-NI", "DE-NW",
                    "DE-RP", "DE-SL", "DE-SN", "DE-ST", "DE-SH", "DE-TH"]
# Level of an issuer of press releases: Germany, a state, the European Union or an international organization
Jurisdiction = Literal["DE", StateCode, "EU", "INT"]

# ---------------------------------------------------------------------------
# Domains of pop culture
# ---------------------------------------------------------------------------

# Fields of pop culture and everyday culture; used by the annotation, the entity registry and the outlets
PopDomain = Literal["Music", "Film and cinema", "TV and streaming", "Celebrities and royals",
                    "Influencers and social media", "Games and esports", "Books and literature",
                    "Podcasts and radio", "Comedy and satire", "Art, stage and museums", "Fashion and beauty",
                    "Food and drink", "Travel and leisure", "Sports", "Fitness and wellness", "Health and body",
                    "Relationships and sexuality", "Family and parenting", "Home and living",
                    "Shopping and consumer products", "Money and career", "Technology and internet",
                    "Religion and spirituality", "Politics and society", "Events and festivals", "Other"]

# ---------------------------------------------------------------------------
# Charts and rankings
# ---------------------------------------------------------------------------

# How a chart is read; every format has a parser in collectors/charts.py
ChartFormat = Literal["apple_rss", "google_trends_rss", "wikimedia_top", "wikimedia_top_country"]
ChartDomain = Literal["music", "podcasts", "apps", "books", "search", "attention"]
# live: the source shows only its current list; day: the source publishes a list per day
ChartPeriod = Literal["live", "day"]
EntryKind = Literal["song", "album", "podcast", "podcast_episode", "app", "book", "audiobook", "search_term", "article"]

# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

EntityType = Literal["person", "music_group", "tv_show", "film_series", "video_game", "podcast", "festival_or_event",
                     "award", "platform", "brand", "product", "sports", "institution", "movement", "concept"]

# ---------------------------------------------------------------------------
# News, press releases and their sources
# ---------------------------------------------------------------------------

OutletType = Literal["public_broadcaster_national", "public_broadcaster_regional", "public_youth_media",
                     "national_daily", "weekly_magazine", "regional_daily", "tabloid", "online_only",
                     "celebrity_magazine", "womens_magazine", "mens_magazine", "lifestyle_magazine",
                     "music_magazine", "film_tv_magazine", "games_magazine", "family_magazine", "youth_magazine",
                     "trade_press"]
PaywallModel = Literal["free", "mixed", "hard"]
Audience = Literal["general", "youth", "women", "men", "parents"]
ActorType = Literal["broadcaster", "streaming_service", "industry_association", "event_organizer", "public_agency",
                    "research_institute", "company"]
ContentBasis = Literal["title_only", "title_teaser", "lead_text"]
Channel = Literal["rss", "news_sitemap"]
ItemDataset = Literal["news_items", "press_releases"]

# ---------------------------------------------------------------------------
# Vocabularies of the annotation
# ---------------------------------------------------------------------------

ArticleType = Literal["News", "Review", "Opinion", "Interview", "Ranking or list", "Guide or advice",
                      "Shopping recommendation", "Gossip", "Feature or portrait", "Live blog", "Quiz or game",
                      "Press release", "Other"]
Tone = Literal["Positive", "Neutral", "Negative", "Mixed"]
WorkKind = Literal["Song", "Album", "Music video", "Film", "TV series", "TV show", "Video game", "Book", "Podcast",
                   "Stage show or musical", "Online video or channel", "Other"]
MentionRole = Literal["Subject", "Recommended", "Criticized", "Commentator", "Mentioned"]
ProductCategory = Literal["Fashion and accessories", "Beauty and cosmetics", "Food", "Drinks", "Alcohol",
                          "Tobacco, vapes and cannabis", "Electronics and gadgets", "Streaming and subscriptions",
                          "Apps and software", "Games and consoles", "Cars and mobility", "Travel and hotels",
                          "Home and furniture", "Sports and fitness", "Health and supplements",
                          "Finance and insurance", "Toys and children's products", "Books and media",
                          "Retail and shops", "Other"]
ProductRole = Literal["Recommended", "Advertised", "Reviewed", "Criticized", "Mentioned"]
Platform = Literal["Netflix", "Amazon Prime Video", "Disney+", "Apple TV", "RTL+", "Joyn", "Paramount+",
                   "Sky and WOW", "ARD Mediathek", "ZDF Mediathek", "YouTube", "TikTok", "Instagram", "Facebook", "X",
                   "Threads", "Bluesky", "Snapchat", "Twitch", "Reddit", "WhatsApp", "Telegram", "Spotify",
                   "Apple Music", "Amazon Music", "Deezer", "Audible", "Steam", "PlayStation", "Xbox", "Nintendo",
                   "OnlyFans", "Tinder and dating apps", "ChatGPT and AI chatbots", "Amazon", "Temu", "Shein",
                   "Other"]
LifestyleTopic = Literal["Diet and nutrition", "Plant-based eating", "Alcohol-free living", "Fitness and exercise",
                         "Mental health and self-care", "Sleep", "Beauty and skin care", "Style and fashion",
                         "Home and interior", "Sustainability", "Minimalism and decluttering", "Travel",
                         "Saving and personal finance", "Career and productivity", "Work-life balance",
                         "Dating and relationships", "Sex and intimacy", "Parenting", "Pets", "Digital detox",
                         "Spirituality and esotericism", "Self-optimization", "Other"]
FamilyForm = Literal["Married couple with children", "Unmarried couple with children", "Couple without children",
                     "Single parent", "Patchwork family", "Same-sex couple", "Rainbow family", "Single",
                     "Open or polyamorous relationship", "Long-distance relationship", "Multigenerational family",
                     "Divorce or separation", "Other"]
# Objects of opinions: state and politics, culture and identity, religion, family and ways of living, consumption
StanceTarget = Literal[
    "Bundeswehr and military", "Military service and conscription", "Police", "Federal government",
    "Political parties and politicians", "AfD", "Democracy", "European Union", "USA", "Russia", "Support for Ukraine",
    "Israel", "Migration and refugees", "Climate protection", "Public broadcasting", "Welfare state",
    "Taxes and the rich", "Gender-neutral language", "Feminism", "LGBTQ rights", "Trans rights",
    "Diversity and inclusion", "Cancel culture and political correctness", "Patriotism and national pride",
    "Tradition and Heimat", "Christianity and the churches", "Catholic Church", "Islam", "Judaism",
    "Religion in general", "Spirituality and esotericism", "Marriage", "Having children", "Childfree living",
    "Traditional gender roles", "Stay-at-home parenting", "Working parents and childcare", "Single life", "Divorce",
    "Open relationships and polyamory", "Same-sex partnerships", "Veganism and vegetarianism", "Meat", "Alcohol",
    "Cannabis", "Smoking and vaping", "Fitness culture", "Body positivity", "Cosmetic surgery and beauty procedures",
    "Weight-loss drugs", "Sustainability and conscious consumption", "Consumerism and shopping",
    "Luxury and wealth", "Flying and air travel", "Cars", "Career ambition", "Work-life balance and shorter hours",
    "Social media", "Smartphones and screen time", "Artificial intelligence", "Influencers", "Streaming services",
]
Stance = Literal["Favorable", "Unfavorable", "Neutral or balanced", "Mixed"]
StanceHolder = Literal["Author or outlet", "Celebrity or influencer", "Fans or audience", "Ordinary people",
                       "Expert or institution", "Politician", "Other"]
# Basic human values of Schwartz (1992)
PromotedValue = Literal["Self-direction", "Stimulation", "Hedonism", "Achievement", "Power and status", "Security",
                        "Conformity", "Tradition", "Benevolence", "Universalism"]
Generation = Literal["Generation Alpha", "Generation Z", "Millennials", "Generation X", "Baby boomers",
                     "Older people"]
UpcomingEventType = Literal["Release", "Premiere or broadcast", "Concert or tour", "Festival", "Award show",
                            "Sports event", "Fair or convention", "Sale or shopping event", "Other"]
CommunicationType = Literal["Market figures or study", "Release or programme announcement", "Award or event",
                            "Personnel", "Policy position", "Partnership or deal", "Other", "Not applicable"]


# ---------------------------------------------------------------------------
# Models filled by Gemini
# ---------------------------------------------------------------------------
# Long vocabularies are plain strings in these models, with the allowed values in the description:
# a response schema with many enumerations exceeds the limit of constrained decoding of the Gemini API.
# The annotation keeps only values of the vocabulary (annotation.clean_annotation); the stored
# records validate them against the Literal types.

def allowed(vocabulary: object) -> str:
    """Returns the values of a Literal vocabulary as text for a field description.

    Args:
        vocabulary: A ``Literal`` alias.

    Returns:
        str: For example ``Allowed values: Positive; Neutral; Negative; Mixed.``
    """
    return "Allowed values: " + "; ".join(str(value) for value in typing.get_args(vocabulary)) + "."


class WorkMention(BaseModel):
    """A work of pop culture that the text refers to."""
    title: str = Field(description="Title of the work as in the text, e.g. 'Babylon Berlin', 'Wicked'. No persons or brands.")
    kind: str = Field(description="Kind of the work. " + allowed(WorkKind))
    role: str = Field(description="Subject if the work is the main topic; Recommended if the text recommends it; Criticized if the text rejects it; Mentioned otherwise. " + allowed(MentionRole))
    tone: Tone = Field(description="How the text presents the work.")


class PersonMention(BaseModel):
    """A public figure or music group that the text refers to."""
    name: str = Field(description="Full name of a public figure (musician, actor, presenter, influencer, athlete, author, politician, royal) or a music group, as commonly written, e.g. 'Helene Fischer', 'Tokio Hotel'. No private persons, no fictional characters.")
    role: str = Field(description="Subject if the person acts or is the main topic; Recommended or Criticized if the text recommends or rejects the person; Commentator if the person only comments; Mentioned otherwise. " + allowed(MentionRole))
    tone: Tone = Field(description="How the text presents the person.")


class ProductMention(BaseModel):
    """A brand, product or service that the text refers to."""
    name: str = Field(description="Brand or product name, e.g. 'Stanley Cup', 'Labubu', 'Dubai-Schokolade', 'Deutschlandticket'. No streaming or social platforms (see platforms).")
    category: str = Field(description="Category of the product. " + allowed(ProductCategory))
    role: str = Field(description="Recommended if the text tells readers to buy or use it; Advertised if the item is an advertisement or sponsored; Reviewed if the text tests it; Criticized if the text advises against it; Mentioned otherwise. " + allowed(ProductRole))


class FamilyFormMention(BaseModel):
    """A form of family or relationship that the text depicts or discusses."""
    form: str = Field(description="The form of family or relationship. " + allowed(FamilyForm))
    tone: Tone = Field(description="How the text presents this form.")


class StanceMention(BaseModel):
    """An opinion that the text expresses or reports about a topic."""
    target: str = Field(description="Object of the opinion. " + allowed(StanceTarget))
    stance: Stance = Field(description="Evaluation of the target by the holder.")
    holder: str = Field(description="Whose opinion it is: the author or outlet, or a person or group quoted in the text. " + allowed(StanceHolder))


class UpcomingEvent(BaseModel):
    """A future event that the text names with a date."""
    event_type: str = Field(description="Type of the upcoming event. " + allowed(UpcomingEventType))
    date: str = Field(description="Date of the event (YYYY-MM-DD). Only events with a stated day.")


class ItemAnnotation(BaseModel):
    """The labels of one article or press release."""
    item_number: int = Field(description="Number of the item in the request, starting at 1.")
    is_pop_culture: bool = Field(description="True if the item is about popular or everyday culture: entertainment, media, celebrities, consumption, lifestyle, leisure, relationships or family life, or the reaction of pop culture to society and politics.")
    domain: str = Field(description="Main field of the item. " + allowed(PopDomain))
    domains_secondary: list[str] = Field(description="Further clearly addressed fields, at most two, with the values of domain. Empty list if none.")
    issue_detail: Optional[str] = Field(description="The specific subject in one to five words, e.g. 'Dschungelcamp finale' or 'Labubu hype'. Null if not pop culture.")
    article_type: ArticleType = Field(description="Form of the item.")
    works: list[WorkMention] = Field(description="Works the item refers to (songs, albums, films, series, shows, games, books, podcasts), each once.")
    people: list[PersonMention] = Field(description="Public figures and music groups the item refers to, each once.")
    products: list[ProductMention] = Field(description="Brands, products and services the item refers to, each once.")
    platforms: list[str] = Field(description="Media, social, shopping and AI platforms the item refers to. Empty list if none. " + allowed(Platform))
    purchase_recommendation: bool = Field(description="True if the item recommends buying, booking or subscribing to something (shopping tips, deals, gift guides, product tests with a recommendation).")
    sponsored: bool = Field(description="True if the item is marked as advertisement, sponsored content or contains affiliate links (e.g. 'Anzeige', 'Werbung', 'Affiliate-Link').")
    lifestyle_advice: list[str] = Field(description="Topics on which the item gives readers advice or recommends a way of living. Empty list if none. " + allowed(LifestyleTopic))
    family_forms: list[FamilyFormMention] = Field(description="Forms of family or relationship the item depicts or discusses, each once. Empty list if none.")
    stances: list[StanceMention] = Field(description="Opinions on the listed targets that the item expresses or reports, at most one per target and holder. Only clear evaluations; empty list if none.")
    promoted_values: list[str] = Field(description="Values the item presents as desirable, at most three. Empty list if none. " + allowed(PromotedValue))
    generations: list[str] = Field(description="Generations the item addresses or discusses. Empty list if none. " + allowed(Generation))
    content_origin_countries: list[str] = Field(description="Countries of origin of the works and people the item is mainly about, as ISO 3166-1 alpha-2 codes, e.g. DE, US, KR. Empty list if unclear.")
    overall_tone: Tone = Field(description="Overall tone of the item.")
    reports_controversy: bool = Field(description="True if the item reports a scandal, shitstorm, boycott, public outrage or a dispute about a person, work, product or statement.")
    upcoming_events: list[UpcomingEvent] = Field(description="Future pop culture events named with a date (releases, premieres, concerts, festivals, award shows, fairs). Empty list if none.")
    communication_type: CommunicationType = Field(description="For press releases: the main purpose of the release. 'Not applicable' for news articles.")


class AnnotationBatch(BaseModel):
    """The model response for a batch of items."""
    items: list[ItemAnnotation] = Field(description="One entry per item of the request, in the order of the request.")


# ---------------------------------------------------------------------------
# Stored records
# ---------------------------------------------------------------------------

class RecordBase(BaseModel):
    """Fields that every record of the dataset has."""
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(description="Stable id of the item. Together with revision it identifies a record.")
    revision: int = Field(default=0, description="0 for the first observation; incremented each time the source changes the item.")
    dataset: str = Field(description="Name of the dataset.")
    schema_version: str = Field(default=SCHEMA_VERSION, description="Version of the record schema.")
    observed_at: datetime = Field(description="UTC time at which the pipeline observed this version of the item. Filtering observed_at <= t gives the state of knowledge at time t.")
    source_id: str = Field(description="Id of the source (chart, API, configuration file, outlet or press source).")
    source_url: Optional[str] = Field(description="URL of the item or of the API resource it was read from.")
    license: str = Field(description="License or terms of the source.")
    backfilled: bool = Field(description="True if the item is older than the start of collection for its dataset, so that observed_at does not reflect when it became public.")


class ChartEntry(BaseModel):
    """One position of a published chart or ranking."""
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(description="Position in the list, starting at 1.")
    title: str = Field(description="Title of the entry: song, album, podcast, episode, app, book, audiobook, search term or article.")
    creator: Optional[str] = Field(description="Artist, author, developer or podcaster as given by the source; the Wikipedia edition for the top articles across editions.")
    item_id: Optional[str] = Field(description="Id of the entry at the source, e.g. the Apple id.")
    url: Optional[str] = Field(description="URL of the entry at the source.")
    genres: list[str] = Field(description="Genres or categories given by the source.")
    release_date: Optional[str] = Field(description="Release date given by the source (YYYY-MM-DD).")
    metric: Optional[float] = Field(description="Value that the source ranks by or states, e.g. views or approximate searches; its meaning is metric_name of the chart.")
    details: dict[str, str] = Field(description="Further values of the source, e.g. the content advisory of a song or the first news article of a trending search.")


class ChartSnapshotRecord(RecordBase):
    """A chart or ranking as published at one observation; stored again only when the source changes it."""
    chart_id: str = Field(description="Stable id of the chart in config/charts.toml, e.g. 'apple.music_top_songs'.")
    chart_name: str = Field(description="Name of the chart.")
    domain: ChartDomain = Field(description="Field of pop culture the chart measures.")
    provider: str = Field(description="Provider id of config/charts.toml.")
    country: Optional[str] = Field(description="Country of the chart (ISO 3166-1 alpha-2); null for a chart of a language edition such as de.wikipedia.")
    period: ChartPeriod = Field(description="live: the current list of a source without history, dated by the day of observation; day: the list of a past day as published by the source.")
    chart_date: date = Field(description="Day the list refers to: the day of observation (UTC) for live lists, the day of the list for daily lists.")
    source_updated: Optional[datetime] = Field(description="Time at which the source states it updated the list (UTC), if given.")
    entry_kind: EntryKind = Field(description="Kind of the entries.")
    metric_name: Optional[str] = Field(description="Meaning of the metric of the entries, e.g. 'views' or 'approximate searches'; null if the chart has no metric.")
    entries: list[ChartEntry] = Field(description="The positions of the list in order of rank.")
    entry_count: int = Field(description="Number of entries.")


class ChartCatalogRecord(RecordBase):
    """An entry of the chart configuration, recorded when it appears or changes."""
    chart_id: str = Field(description="Stable id of the chart, '<provider>.<chart>'.")
    name: str = Field(description="Name of the chart.")
    domain: ChartDomain = Field(description="Field of pop culture the chart measures.")
    provider: str = Field(description="Provider id.")
    format: ChartFormat = Field(description="How the chart is read; the name of its parser.")
    url: str = Field(description="Address of the chart; the address of a daily list contains {year}, {month} and {day}.")
    country: Optional[str] = Field(description="Country of the chart (ISO 3166-1 alpha-2); null for a language edition.")
    period: ChartPeriod = Field(description="live or day.")
    entry_kind: EntryKind = Field(description="Kind of the entries.")
    max_entries: int = Field(description="Number of entries that are stored at most.")
    added: date = Field(description="Day on which the chart was added; start of collection.")
    removed: Optional[date] = Field(description="Day on which the chart was removed; end of collection.")
    removed_reason: Optional[str] = Field(description="Why the chart was removed.")


class EntityRecord(RecordBase):
    """An entry of the entity registry, recorded when it appears or changes."""
    entity_id: str = Field(description="Stable id of the entity, e.g. 'helene_fischer' or 'bundeswehr'.")
    name: str = Field(description="Name of the entity.")
    entity_type: EntityType = Field(description="Type of the entity.")
    domain: PopDomain = Field(description="Field of pop culture or society the entity belongs to.")
    wikipedia_title: str = Field(description="Title of the German Wikipedia article as configured; resolved through redirects at run time.")
    added: date = Field(description="Day on which the entity was added to the registry.")
    removed: Optional[date] = Field(description="Day on which the entity was removed from the registry.")
    removed_reason: Optional[str] = Field(description="Why the entity was removed.")


class PageviewRecord(RecordBase):
    """Daily views of the German Wikipedia article of a registered entity."""
    entity_id: str = Field(description="Id of the entity in the registry.")
    entity_type: EntityType = Field(description="Type of the entity.")
    domain: PopDomain = Field(description="Field of the entity.")
    wikipedia_project: str = Field(description="Wikipedia edition, de.wikipedia.")
    wikipedia_title: str = Field(description="Title of the article at the time of the request.")
    wikidata_qid: Optional[str] = Field(description="Wikidata id of the article, the key to the people of text_annotations and to other datasets.")
    daily_views: dict[date, int] = Field(description="Views by human users per day (UTC), for the days since the previous record.")


class NewsItemRecord(RecordBase):
    """Metadata of one news article. The text is not stored."""
    outlet_id: str = Field(description="Id of the outlet.")
    outlet_type: OutletType = Field(description="Type of the outlet.")
    owner_group: str = Field(description="Publisher group of the outlet, or 'public' for public broadcasters.")
    home_state: Optional[StateCode] = Field(description="State of a regional outlet; null for national outlets.")
    outlet_focus: Optional[PopDomain] = Field(description="Field of a specialist outlet; null for general outlets.")
    audience: Audience = Field(description="Main audience of the outlet.")
    channel: Channel = Field(description="Where the article was found: RSS/Atom feed or news sitemap.")
    url: str = Field(description="URL of the article.")
    canonical_url: Optional[str] = Field(description="Canonical URL declared by the article page.")
    title: str = Field(description="Title of the article.")
    published_at: Optional[datetime] = Field(description="Publication time (UTC).")
    updated_at: Optional[datetime] = Field(description="Last modification time from the article page (UTC).")
    section: Optional[str] = Field(description="Section of the outlet, from the page or the feed.")
    feed_categories: list[str] = Field(description="Categories of the item in the feed.")
    keywords: list[str] = Field(description="Keywords from the article page or the news sitemap.")
    jsonld_type: Optional[str] = Field(description="schema.org type of the article page, e.g. NewsArticle or ReviewNewsArticle.")
    is_agency_copy: Optional[bool] = Field(description="True if the article is credited to a news agency (dpa, AFP, Reuters, epd, KNA, AP, SID). Null without page data.")
    author_count: Optional[int] = Field(description="Number of named authors. Author names are not stored. Null without page data.")
    word_count: Optional[int] = Field(description="Word count from the article page. Null if not given.")
    is_paywalled: Optional[bool] = Field(description="True if the page marks the article as not accessible for free. Null without page data.")
    has_video: Optional[bool] = Field(description="True if the article page embeds a video. Null without page data.")
    content_basis: ContentBasis = Field(description="Text available for the annotation: title only, title and teaser, or lead text of the page.")


class PressReleaseRecord(RecordBase):
    """Metadata of one press release. The text is not stored."""
    press_source_id: str = Field(description="Id of the press source.")
    actor_type: ActorType = Field(description="Type of the issuing actor.")
    jurisdiction: Jurisdiction = Field(description="Level of the issuer: DE, a state (ISO 3166-2), EU or INT.")
    url: str = Field(description="URL of the press release.")
    canonical_url: Optional[str] = Field(description="Canonical URL declared by the page.")
    title: str = Field(description="Title of the press release.")
    published_at: Optional[datetime] = Field(description="Publication time (UTC).")
    feed_categories: list[str] = Field(description="Categories of the item in the feed.")
    content_basis: ContentBasis = Field(description="Text available for the annotation.")


class WorkLabel(BaseModel):
    """A work named in an annotated item."""
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="Title of the work as returned by the model.")
    kind: WorkKind = Field(description="Kind of the work.")
    role: MentionRole = Field(description="Role of the work in the item.")
    tone: Tone = Field(description="How the item presents the work.")


class PersonLabel(BaseModel):
    """A public figure or music group of an annotated item, resolved to the German Wikipedia."""
    model_config = ConfigDict(extra="forbid")

    wikidata_qid: str = Field(description="Wikidata id of the article.")
    wikipedia_title: str = Field(description="Title of the German Wikipedia article.")
    role: MentionRole = Field(description="Role of the person in the item.")
    tone: Tone = Field(description="How the item presents the person.")


class ProductLabel(BaseModel):
    """A brand, product or service named in an annotated item."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Brand or product name as returned by the model.")
    category: ProductCategory = Field(description="Category of the product.")
    role: ProductRole = Field(description="Role of the product in the item.")


class StanceLabel(BaseModel):
    """An opinion expressed or reported in an annotated item."""
    model_config = ConfigDict(extra="forbid")

    target: StanceTarget = Field(description="Object of the opinion.")
    stance: Stance = Field(description="Evaluation of the target.")
    holder: StanceHolder = Field(description="Whose opinion it is.")


class EventLabel(BaseModel):
    """A future event named with a date in an annotated item."""
    model_config = ConfigDict(extra="forbid")

    event_type: UpcomingEventType = Field(description="Type of the event.")
    date: str = Field(description="Day of the event (YYYY-MM-DD).")


class TextAnnotationRecord(RecordBase):
    """Gemini labels of one news article or press release."""
    item_dataset: ItemDataset = Field(description="Dataset of the annotated item.")
    item_id: str = Field(description="record_id of the annotated item.")
    model: str = Field(description="Gemini model that produced the labels.")
    extraction_hash: str = Field(description="Hash of the prompt, response schema and model settings. Labels with the same hash were produced under the same conditions.")
    content_basis: ContentBasis = Field(description="Text the model saw: title only, title and teaser, or lead text.")
    is_pop_culture: bool = Field(description="Whether the item is about popular or everyday culture.")
    domain: PopDomain = Field(description="Main field of the item.")
    domains_secondary: list[PopDomain] = Field(description="Further fields of the item.")
    issue_detail: Optional[str] = Field(description="Specific subject in a few words.")
    article_type: ArticleType = Field(description="Form of the item.")
    works: list[WorkLabel] = Field(description="Works the item refers to, with kind, role and tone.")
    people: list[PersonLabel] = Field(description="Public figures and music groups with a German Wikipedia article, with role and tone.")
    unresolved_person_count: int = Field(description="Number of named persons without a German Wikipedia article; their names are not stored.")
    products: list[ProductLabel] = Field(description="Brands, products and services with category and role.")
    platforms: list[Platform] = Field(description="Platforms the item refers to.")
    purchase_recommendation: bool = Field(description="Whether the item recommends buying, booking or subscribing to something.")
    sponsored: bool = Field(description="Whether the item is marked as advertisement, sponsored or with affiliate links.")
    lifestyle_advice: list[LifestyleTopic] = Field(description="Topics on which the item gives advice on a way of living.")
    family_forms: dict[FamilyForm, Tone] = Field(description="Forms of family or relationship the item depicts, with the tone of their presentation.")
    stances: list[StanceLabel] = Field(description="Opinions on fixed targets, with stance and holder.")
    promoted_values: list[PromotedValue] = Field(description="Values the item presents as desirable (Schwartz).")
    generations: list[Generation] = Field(description="Generations the item addresses or discusses.")
    content_origin_countries: list[str] = Field(description="Countries of origin of the works and people of the item (ISO 3166-1 alpha-2).")
    overall_tone: Tone = Field(description="Overall tone of the item.")
    reports_controversy: bool = Field(description="Whether the item reports a scandal, shitstorm, boycott or public dispute.")
    upcoming_events: list[EventLabel] = Field(description="Future events with type and date.")
    communication_type: CommunicationType = Field(description="Purpose of a press release; 'Not applicable' for news.")


DATASET_MODELS: dict[str, type[RecordBase]] = {
    "chart_snapshots": ChartSnapshotRecord,
    "chart_catalog": ChartCatalogRecord,
    "entities": EntityRecord,
    "wikipedia_pageviews": PageviewRecord,
    "news_items": NewsItemRecord,
    "press_releases": PressReleaseRecord,
    "text_annotations": TextAnnotationRecord,
}

# Nested models of the records, listed in the schema snapshot
NESTED_MODELS: tuple[type[BaseModel], ...] = (ChartEntry, WorkLabel, PersonLabel, ProductLabel, StanceLabel,
                                              EventLabel)

# Models whose text form goes into the extraction hash
ANNOTATION_MODELS: tuple[type[BaseModel], ...] = (
    WorkMention, PersonMention, ProductMention, FamilyFormMention, StanceMention, UpcomingEvent, ItemAnnotation,
    AnnotationBatch,
)


def _describe_type(annotation: object) -> str:
    """Returns a text form of a type annotation that is the same on all supported Python versions.

    Args:
        annotation: A type annotation of a model field.

    Returns:
        str: For example ``list[WorkMention]`` or ``Union[NoneType,str]``.
    """
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is Literal:
        return "Literal[" + ",".join(repr(arg) for arg in args) + "]"
    if origin is typing.Union or origin is types.UnionType:
        return "Union[" + ",".join(sorted(_describe_type(arg) for arg in args)) + "]"
    if origin is list:
        return "list[" + _describe_type(args[0]) + "]"
    if origin is dict:
        return "dict[" + _describe_type(args[0]) + "," + _describe_type(args[1]) + "]"
    if isinstance(annotation, type):
        return annotation.__name__
    return repr(annotation)


def describe_models(models: typing.Iterable[type[BaseModel]], include_descriptions: bool) -> str:
    """Returns a canonical text form of models.

    The text lists every field with its type and whether it is required. It is
    used for the extraction hash (annotation models, with descriptions) and for
    the schema snapshot test (all models, without descriptions).

    Args:
        models: The models to describe.
        include_descriptions: Whether to include the field descriptions.

    Returns:
        str: One line per model and per field.
    """
    lines = []
    for model in models:
        lines.append(model.__name__)
        for name, field in model.model_fields.items():
            line = f"  {name}: {_describe_type(field.annotation)} required={field.is_required()}"
            if include_descriptions:
                line += f" description={field.description!r}"
            lines.append(line)
    return "\n".join(lines)
