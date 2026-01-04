import { useState, useEffect } from "react";
import { useToast } from "@/components/ui/use-toast";
import FileUploadZone from "@/components/FileUploadZone";
import RedactionMethodSelect from "@/components/RedactionMethodSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2, Download, X, Settings2, Upload, Shield } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

interface EntityType {
  code: string;
  label: string;
  enabled: boolean;
}

const Index = () => {
  // State for file upload
  const [files, setFiles] = useState<File[]>([]);

  // State for redaction
  const [method, setMethod] = useState("full_redact");
  const [replaceText, setReplaceText] = useState("[REDACTED]");
  const [isRedacting, setIsRedacting] = useState(false);

  // State for custom keywords
  const [keywords, setKeywords] = useState<string[]>([]);
  const [keywordInput, setKeywordInput] = useState("");
  const [matchMode, setMatchMode] = useState<"exact" | "fuzzy" | "regex">("exact");
  const [fuzzyThreshold, setFuzzyThreshold] = useState(85);

  // State for entity types
  const [entityTypes, setEntityTypes] = useState<EntityType[]>([]);
  const [isEntitySettingsOpen, setIsEntitySettingsOpen] = useState(false);

  const { toast } = useToast();

  useEffect(() => {
    fetchEntityTypes();
  }, []);

  const fetchEntityTypes = async () => {
    try {
      const apiUrl = import.meta.env.VITE_API_URL || "http://localhost:5000";
      const response = await fetch(`${apiUrl}/entity-types`, {
        method: "GET",
        headers: { "Accept": "application/json" },
        mode: "cors",
        credentials: "omit",
      });

      if (!response.ok) throw new Error("Failed to fetch entity types");

      const data = await response.json();
      const types: EntityType[] = Object.entries(data.entity_types).map(
        ([code, label]) => ({
          code,
          label: label as string,
          enabled: data.default_entities.includes(code),
        })
      );
      setEntityTypes(types);
    } catch (error) {
      console.error("Error fetching entity types:", error);
      setEntityTypes([
        { code: "PERSON", label: "Names", enabled: true },
        { code: "EMAIL_ADDRESS", label: "Email Addresses", enabled: true },
        { code: "PHONE_NUMBER", label: "Phone Numbers", enabled: true },
        { code: "CREDIT_CARD", label: "Credit Card Numbers", enabled: true },
        { code: "US_SSN", label: "Social Security Numbers (US)", enabled: true },
        { code: "LOCATION", label: "Locations/Addresses", enabled: true },
        { code: "DATE_TIME", label: "Dates & Times", enabled: true },
      ]);
    }
  };

  const handleFilesChange = (newFiles: File[]) => {
    const pdfFiles = newFiles.filter(
      (file) => file.type === "application/pdf"
    );
    if (pdfFiles.length !== newFiles.length) {
      toast({
        variant: "destructive",
        title: "Invalid files",
        description: "Only PDF files are supported",
      });
    }
    setFiles(pdfFiles);
  };

  const handleAddKeyword = () => {
    const trimmed = keywordInput.trim();
    if (trimmed && !keywords.includes(trimmed)) {
      setKeywords((prev) => [...prev, trimmed]);
      setKeywordInput("");
    }
  };

  const handleKeywordKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAddKeyword();
    }
  };

  const handleRemoveKeyword = (keyword: string) => {
    setKeywords((prev) => prev.filter((k) => k !== keyword));
  };

  const handleToggleEntity = (code: string) => {
    setEntityTypes((prev) =>
      prev.map((e) => (e.code === code ? { ...e, enabled: !e.enabled } : e))
    );
  };

  const handleToggleAllEntities = (enabled: boolean) => {
    setEntityTypes((prev) => prev.map((e) => ({ ...e, enabled })));
  };

  const handleRedact = async () => {
    if (files.length === 0) {
      toast({
        variant: "destructive",
        title: "No files selected",
        description: "Please upload at least one PDF file to redact",
      });
      return;
    }

    setIsRedacting(true);

    try {
      const formData = new FormData();
      files.forEach((file) => formData.append("files", file));
      formData.append("method", method);
      
      if (method === "replace") {
        formData.append("replace_text", replaceText);
      }

      // Add keywords if any
      if (keywords.length > 0) {
        formData.append("keywords", JSON.stringify(keywords));
        formData.append("match_mode", matchMode);
        if (matchMode === "fuzzy") {
          formData.append("fuzzy_threshold", fuzzyThreshold.toString());
        }
      }

      // Add enabled entity types
      const enabledEntities = entityTypes
        .filter((e) => e.enabled)
        .map((e) => e.code);
      formData.append("enabled_entities", JSON.stringify(enabledEntities));

      const apiUrl = import.meta.env.VITE_API_URL || "http://localhost:5000";
      const response = await fetch(`${apiUrl}/redact`, {
        method: "POST",
        body: formData,
        mode: "cors",
        credentials: "omit",
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || "Failed to redact documents");
      }

      // Download the ZIP file
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `redacted_${new Date().getTime()}.zip`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      toast({
        title: "Success!",
        description: `Redacted ${files.length} document(s). Download started.`,
      });

      // Clear files after successful redaction
      setFiles([]);
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Redaction Error",
        description: err instanceof Error ? err.message : "An unexpected error occurred",
      });
    } finally {
      setIsRedacting(false);
    }
  };

  return (
    <div className="min-h-screen bg-background">
      <div className="container mx-auto px-4 py-8 max-w-4xl">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="flex items-center justify-center gap-2 mb-2">
            <Shield className="h-8 w-8 text-primary" />
            <h1 className="text-3xl font-bold text-foreground">RedactLy.AI</h1>
          </div>
          <p className="text-muted-foreground">
            Fast, accurate PII redaction for your PDF documents
          </p>
        </div>

        <div className="grid gap-6">
          {/* File Upload Card */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Upload size={20} />
                Upload Documents
              </CardTitle>
              <CardDescription>
                Drop PDF files here or click to browse
              </CardDescription>
            </CardHeader>
            <CardContent>
              <FileUploadZone files={files} onFileChange={handleFilesChange} />
            </CardContent>
          </Card>

          {/* Redaction Settings Card */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Settings2 size={20} />
                Redaction Settings
              </CardTitle>
              <CardDescription>
                Configure what to redact and how
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Custom Keywords Section */}
              <div className="space-y-3">
                <Label>Custom Keywords to Redact</Label>
                <div className="flex gap-2">
                  <Input
                    type="text"
                    value={keywordInput}
                    onChange={(e) => setKeywordInput(e.target.value)}
                    onKeyDown={handleKeywordKeyDown}
                    placeholder={matchMode === "regex" ? "Enter regex pattern..." : "Enter keyword..."}
                    className="flex-1"
                  />
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={handleAddKeyword}
                    disabled={!keywordInput.trim()}
                  >
                    Add
                  </Button>
                </div>
                {keywords.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-2">
                    {keywords.map((keyword) => (
                      <Badge
                        key={keyword}
                        variant="secondary"
                        className="flex items-center gap-1 px-2 py-1"
                      >
                        {keyword}
                        <button
                          type="button"
                          onClick={() => handleRemoveKeyword(keyword)}
                          className="ml-1 hover:text-destructive"
                        >
                          <X size={14} />
                        </button>
                      </Badge>
                    ))}
                  </div>
                )}
              </div>

              {/* Match Mode Selection */}
              <div className="space-y-2">
                <Label>Keyword Matching Mode</Label>
                <Select
                  value={matchMode}
                  onValueChange={(value: "exact" | "fuzzy" | "regex") => setMatchMode(value)}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Select matching mode" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="exact">Exact Match (case-insensitive)</SelectItem>
                    <SelectItem value="fuzzy">Fuzzy Match (tolerates typos)</SelectItem>
                    <SelectItem value="regex">Regex Pattern (advanced)</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Fuzzy Threshold Slider */}
              {matchMode === "fuzzy" && (
                <div className="space-y-3">
                  <div className="flex justify-between">
                    <Label>Fuzzy Match Threshold</Label>
                    <span className="text-sm text-muted-foreground">{fuzzyThreshold}%</span>
                  </div>
                  <Slider
                    value={[fuzzyThreshold]}
                    onValueChange={(value) => setFuzzyThreshold(value[0])}
                    min={50}
                    max={100}
                    step={5}
                    className="w-full"
                  />
                  <p className="text-xs text-muted-foreground">
                    Lower values match more variations, higher values require closer matches
                  </p>
                </div>
              )}

              {/* Entity Types Toggle */}
              <Collapsible open={isEntitySettingsOpen} onOpenChange={setIsEntitySettingsOpen}>
                <CollapsibleTrigger asChild>
                  <Button variant="outline" className="w-full justify-between">
                    <span className="flex items-center gap-2">
                      <Settings2 size={16} />
                      PII Entity Types
                    </span>
                    <span className="text-muted-foreground text-sm">
                      {entityTypes.filter((e) => e.enabled).length} of {entityTypes.length} enabled
                    </span>
                  </Button>
                </CollapsibleTrigger>
                <CollapsibleContent className="mt-3">
                  <div className="border rounded-md p-4 space-y-3">
                    <div className="flex justify-between items-center pb-2 border-b">
                      <span className="text-sm font-medium">Auto-detect these PII types:</span>
                      <div className="flex gap-2">
                        <Button variant="ghost" size="sm" onClick={() => handleToggleAllEntities(true)}>
                          Enable All
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => handleToggleAllEntities(false)}>
                          Disable All
                        </Button>
                      </div>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-48 overflow-y-auto">
                      {entityTypes.map((entity) => (
                        <div key={entity.code} className="flex items-center space-x-2">
                          <Checkbox
                            id={`entity-${entity.code}`}
                            checked={entity.enabled}
                            onCheckedChange={() => handleToggleEntity(entity.code)}
                          />
                          <label htmlFor={`entity-${entity.code}`} className="text-sm cursor-pointer">
                            {entity.label}
                          </label>
                        </div>
                      ))}
                    </div>
                  </div>
                </CollapsibleContent>
              </Collapsible>

              {/* Redaction Method */}
              <div className="space-y-2">
                <Label>Redaction Method</Label>
                <RedactionMethodSelect selected={method} onSelect={setMethod} />
              </div>

              {method === "replace" && (
                <div className="space-y-2">
                  <Label htmlFor="replaceText">Replacement Text</Label>
                  <Input
                    id="replaceText"
                    type="text"
                    value={replaceText}
                    onChange={(e) => setReplaceText(e.target.value)}
                    placeholder="Enter text to replace sensitive information"
                  />
                </div>
              )}
            </CardContent>
          </Card>

          {/* Redact Button */}
          <Button
            onClick={handleRedact}
            disabled={isRedacting || files.length === 0}
            size="lg"
            className="w-full"
          >
            {isRedacting ? (
              <>
                <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                Redacting...
              </>
            ) : (
              <>
                <Download className="mr-2 h-5 w-5" />
                Redact & Download ({files.length} file{files.length !== 1 ? "s" : ""})
              </>
            )}
          </Button>
        </div>

        {/* How It Works */}
        <div className="mt-8 bg-accent border border-accent rounded-xl p-6">
          <h3 className="text-lg font-semibold text-accent-foreground mb-4">How It Works</h3>
          <div className="grid gap-4 md:grid-cols-3">
            <div className="flex items-start gap-3">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground font-medium">
                1
              </div>
              <div>
                <p className="text-accent-foreground font-medium">Upload PDFs</p>
                <p className="text-sm text-accent-foreground/80">
                  Drop your PDF files into the upload zone
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground font-medium">
                2
              </div>
              <div>
                <p className="text-accent-foreground font-medium">Configure</p>
                <p className="text-sm text-accent-foreground/80">
                  Add keywords and select entity types to redact
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground font-medium">
                3
              </div>
              <div>
                <p className="text-accent-foreground font-medium">Download</p>
                <p className="text-sm text-accent-foreground/80">
                  Get your redacted documents as a ZIP file
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Index;
