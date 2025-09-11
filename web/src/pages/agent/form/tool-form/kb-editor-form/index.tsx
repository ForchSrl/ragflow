import { FormContainer } from '@/components/form-container';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useFetchKnowledgeList } from '@/hooks/knowledge-hooks';
import { zodResolver } from '@hookform/resolvers/zod';
import { t } from 'i18next';
import { useForm, useWatch } from 'react-hook-form';
import { z } from 'zod';
import { FormWrapper } from '../../components/form-wrapper';
import { useValues } from '../use-values';
import { useWatchFormChange } from '../use-watch-change';

const FormSchema = z.object({
  action: z.string().default('append_chunk'),
  dataset: z.string().optional(),
  document_id: z.string().optional(),
  document_name: z.string().optional(),
  chunk_id: z.string().optional(),
  content: z.string().optional(),
  filename: z.string().optional(),
  mime: z.string().optional(),
  chunk_method: z.string().optional(),
  parser_config: z.string().optional(),
  parse: z.boolean().optional(),
  enabled: z.boolean().optional(),
  important_keywords: z.string().optional(),
  questions: z.string().optional(),
});

const actions = [
  { label: 'append_chunk', value: 'append_chunk' },
  { label: 'replace_document', value: 'replace_document' },
  { label: 'delete_document', value: 'delete_document' },
  { label: 'delete_chunk', value: 'delete_chunk' },
];

export default function KBEditorForm() {
  const defaults = useValues();
  const form = useForm<z.infer<typeof FormSchema>>({
    defaultValues: defaults as any,
    resolver: zodResolver(FormSchema),
  });

  useWatchFormChange(form);

  const selectedAction = useWatch({ control: form.control, name: 'action' });
  const { list: kbList } = useFetchKnowledgeList(true);

  // Backward-compat: if legacy dataset_id exists in defaults, prefill dataset
  // @ts-ignore
  const legacyDatasetId = (defaults as any)?.dataset_id as string | undefined;
  if (!form.getValues('dataset') && legacyDatasetId) {
    form.setValue('dataset', legacyDatasetId);
  }

  return (
    <Form {...form}>
      <FormWrapper>
        <FormContainer>
          <FormField
            control={form.control}
            name="action"
            render={({ field }) => (
              <FormItem className="flex-1">
                <FormLabel>Action</FormLabel>
                <FormControl>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select action" />
                    </SelectTrigger>
                    <SelectContent>
                      {actions.map((a) => (
                        <SelectItem key={a.value} value={a.value}>
                          {a.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </FormControl>
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="dataset"
            render={({ field }) => (
              <FormItem className="flex-1">
                <FormLabel>{t('chat.knowledgeBases')}</FormLabel>
                <FormControl>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select dataset (or set a variable/id below)" />
                    </SelectTrigger>
                    <SelectContent>
                      {kbList.map((kb) => (
                        <SelectItem key={kb.id} value={kb.id}>
                          {kb.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </FormControl>
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="dataset"
            render={({ field }) => (
              <FormItem className="flex-1">
                <FormLabel>Dataset Id or Name (supports variable)</FormLabel>
                <FormControl>
                  <Input
                    {...field}
                    placeholder="e.g., 50bf... or SalesKB or {begin@dataset}"
                  />
                </FormControl>
              </FormItem>
            )}
          />

          {(selectedAction === 'append_chunk' ||
            selectedAction === 'replace_document') && (
            <>
              <FormField
                control={form.control}
                name="content"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormLabel>Content</FormLabel>
                    <FormControl>
                      <Textarea
                        {...field}
                        rows={6}
                        placeholder="Chunk or document content"
                      />
                    </FormControl>
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="filename"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormLabel>Filename</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="update.md" />
                    </FormControl>
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="mime"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormLabel>MIME</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="text/markdown" />
                    </FormControl>
                  </FormItem>
                )}
              />
            </>
          )}

          {selectedAction === 'append_chunk' && (
            <>
              <FormField
                control={form.control}
                name="document_id"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormLabel>Document Id</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        placeholder="Optional: existing document id"
                      />
                    </FormControl>
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="document_name"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormLabel>Document Name</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        placeholder="Optional: resolve id by name"
                      />
                    </FormControl>
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="important_keywords"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormLabel>Important Keywords</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="Comma-separated" />
                    </FormControl>
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="questions"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormLabel>Questions</FormLabel>
                    <FormControl>
                      <Textarea
                        {...field}
                        rows={3}
                        placeholder="Newline-separated"
                      />
                    </FormControl>
                  </FormItem>
                )}
              />
            </>
          )}

          {selectedAction === 'delete_chunk' && (
            <>
              <FormField
                control={form.control}
                name="document_id"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormLabel>Document Id</FormLabel>
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="chunk_id"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormLabel>Chunk Id</FormLabel>
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
                  </FormItem>
                )}
              />
            </>
          )}

          {selectedAction === 'replace_document' && (
            <>
              <FormField
                control={form.control}
                name="chunk_method"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormLabel>Chunk Method</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="naive|manual|qa|..." />
                    </FormControl>
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="parser_config"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormLabel>Parser Config (JSON)</FormLabel>
                    <FormControl>
                      <Textarea {...field} rows={4} placeholder="{}" />
                    </FormControl>
                  </FormItem>
                )}
              />
              <div className="flex gap-4">
                <FormField
                  control={form.control}
                  name="parse"
                  render={({ field }) => (
                    <FormItem className="flex-1">
                      <FormLabel>Parse After Upload</FormLabel>
                      <FormControl>
                        <Switch
                          checked={!!field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="enabled"
                  render={({ field }) => (
                    <FormItem className="flex-1">
                      <FormLabel>Enabled</FormLabel>
                      <FormControl>
                        <Switch
                          checked={!!field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                    </FormItem>
                  )}
                />
              </div>
            </>
          )}
        </FormContainer>
      </FormWrapper>
    </Form>
  );
}
